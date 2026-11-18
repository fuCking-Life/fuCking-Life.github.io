覆盖范围：TradingAgents v0.7.1（TauricResearch 开源的 LangGraph 多 agent 交易框架），2026-11-03 读架构，2026-11-19 拆"什么才算 agent"。

这一篇记两件事：一是什么东西才算 agent，二是这个项目把自主权放给了模型的哪几个地方。结论先写在这：**下一步做什么、什么时候停，由模型决定的才是 agent；规则事先就能写死的部分，交给代码。**

<!--more-->

## 项目一览：一张有共享状态的图

TradingAgents 模拟一家交易公司：分析师先分头调研，研究员辩论多空，交易员出方案，风控团队再辩一轮，最后由组合经理拍板。整张图在 `tradingagents/graph/setup.py` 里连起来：

```text
          ┌─ Market Analyst ──────┐
          ├─ Sentiment Analyst ───┤
START ────┼─ News Analyst ────────┼──► Bull ⇄ Bear ──► Research Manager ──► Trader
          ├─ Fundamentals Analyst ┤      (辩论 N 轮)
          └─ Memory Log ──────────┘

Trader ──► Aggressive → Conservative → Neutral ─(循环 N 轮)─► Portfolio Manager ──► END
```

各个角色之间不靠聊天消息通信，而是读写同一个 `AgentState`（`agents/state.py`）：分析师写 `market_report` 这类字段，辩论写 `investment_debate_state`，经理写 `investment_plan` 和 `final_trade_decision`。下游只拿到上游的成品报告，拿不到上游的工具调用过程。

> **要点：** 多 agent 之间传递的是结构化的状态字段，而不是一长串对话，这样每个角色的上下文都是干净、可控的。

## 什么才算 agent

项目号称有 12 个 agent。拿其中两个的核心代码对比：

```python
# Market Analyst：子图 agent ⇄ tools，最多 20 轮
result = (prompt | llm.bind_tools(TOOLS)).invoke(messages)
if result.tool_calls:   # 去执行工具，结果追加进 messages，再回到 agent
else:                   # 结束，result.content 就是报告

# Bull Researcher
response = llm.invoke(prompt)   # prompt 里塞好了所有报告和辩论历史
```

> **我的理解：** 第一个绑定了一组工具，是一个 ReAct 循环：模型根据 prompt 选工具、拿到结果后再决定下一步，所以是 agent。第二个只是把一个 prompt 喂给大模型、拿回一个回答，不算 agent。

> **修正：** 我一开始说模型会"反思"。其实 ReAct 循环里没有单独的反思步骤，流程是：模型输出"我要调用某个工具" → 代码执行工具、把结果追加进 messages → 模型看完整个历史，再决定继续调工具还是写报告。所谓"思考"，只是模型在做决定前生成的一段文字。

```text
模型输出: "调用 get_stock_data(2026-01-01, 2026-01-09)"   ← 模型决定
代码执行工具，把结果追加进 messages                         ← 代码只负责执行
模型再看一遍全部历史: 再调一个工具？还是写报告？         ← 模型又决定一次
```

> **原则：** 判断是不是 agent，看的是控制流：下一步做什么、什么时候停，是不是由模型决定。

## 辩论是状态机，不是 agent

Bull 和 Bear 来回辩好几轮，看起来也是一个循环。但决定下一个是谁发言的是 `graph/conditional_logic.py`：

```python
def should_continue_debate(self, state):
    if state["investment_debate_state"]["count"] >= 2 * self.max_debate_rounds:
        return "Research Manager"
    if state["investment_debate_state"]["current_response"].startswith("Bull"):
        return "Bear Researcher"
    return "Bull Researcher"
```

数一下次数、看一眼上一个是谁发言，就决定下一步，全程没有模型参与，所以整个辩论是一个**状态机**，不是 agent。LangGraph 的图本质上就是状态机：节点读写 state，条件边根据 state 决定往哪走。

为什么不让一个 LLM 主持人来调度？

- **防止乱序**：交给模型调度，可能出现 Bull 连说两次，或者辩论一直不结束。
- **成本可预测**：辩论 1 轮就是正好 2 次 LLM 调用，不会因为模型觉得"还没辩够"一直跑下去。
- **这一步不需要判断力**：轮流发言、到次数就停，规则事先完全确定，交给模型只会多出不确定性，没有任何收益。

> **原则：** 需要判断力的决策交给模型，事先能写成规则的决策交给代码。

## 自主权放在了哪里

用这条原则重新看整个项目：外层是一条固定的流程（workflow），只有其中几个分析师内部是真正的 agent 循环。作者只在很少的地方把自主权交给模型。

而且同样是分析师，待遇也不一样：

| | Market Analyst | Sentiment Analyst |
| --- | --- | --- |
| 怎么拿数据 | 模型自己决定调哪些工具、调几次 | 代码先抓好新闻、StockTwits、Reddit，塞进 prompt |
| 是不是 agent | 是 | 不是，只调用一次 LLM |

> **思考题：** 按上面那条原则，为什么 Market Analyst 可以自己决定调用哪些工具，Sentiment Analyst 却不行？提示：Market Analyst 的 prompt 列了十几个技术指标，要求它挑出最多 8 个。

## 给 agent 自主权时，项目加的几道护栏

这几处是 11-03 读代码时记下的，正好都是在"放给模型的那几步"上加的约束：

- **每个分析师是一个独立子图**：子图只把报告交回主图，并行的分析师不会写同一个字段，工具调用记录也不会混进彼此的历史。
- **工具轮数上限**：调用工具超过 `max_tool_rounds`（默认 20）轮，就追加一句"工具次数用完了，现在写报告"，并且这一轮不再绑定工具，防止模型无限调用工具、跑爆递归上限。
- **工具参数由状态注入**：`symbol` 和 `trade_date` 用 `InjectedState` 从状态里注入，模型看不到也改不了；日期再被截断到分析日，回测时模型拿不到未来数据。
- **关键决策用结构化输出**：Research Manager、Trader、Portfolio Manager 输出 Pydantic 对象，失败了再退回自由文本。最终评级直接取枚举值，不从文本里解析，避免"不是 Sell"这类字样被读成 Sell。
- **记忆是延迟反馈**：每次的决策先以 pending 状态记下来，到期后对比真实收益写一条 2~4 句话的教训，下次只注入给 Portfolio Manager。所谓"学习"不是微调模型，而是把过去的结论塞回 prompt。

## 下一步

- 先回答上面那道思考题。
- 用一个 ticker 完整跑一遍，对照 `full_states_log_*.json`，看 state 在每个节点之后怎么变化。
- 再看记忆闭环：`memory/log.py`、`memory/settlement.py`、`memory/reflection.py`。
