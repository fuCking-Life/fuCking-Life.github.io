覆盖范围：TradingAgents v0.7.1（TauricResearch 开源的 LangGraph 多 agent 交易框架）。今天聊了三件事：什么才算 agent、辩论为什么是状态机、这个项目把自主权放在了哪里。

今天最大的收获是一个判断标准：**下一步做什么、什么时候停，由模型决定的才是 agent；规则事先就能写死的部分，交给代码。**两道判断题我都答对了，但有两处说法不准：一是以为 ReAct 里有"反思"这一步，二是把"有工具"当成了 agent 的标志。还有一道题没答完。

<!--more-->

## 背景：这个项目长什么样

TradingAgents 模拟一家交易公司：分析师先分头调研，研究员辩论多空，交易员出方案，风控团队再辩一轮，最后由组合经理拍板。整张图在 `tradingagents/graph/setup.py` 里连起来：

```text
          ┌─ Market Analyst ──────┐
          ├─ Sentiment Analyst ───┤
START ────┼─ News Analyst ────────┼──► Bull ⇄ Bear ──► Research Manager ──► Trader
          ├─ Fundamentals Analyst ┤      (辩论 N 轮)
          └─ Memory Log ──────────┘

Trader ──► Aggressive → Conservative → Neutral ─(循环 N 轮)─► Portfolio Manager ──► END
```

各个角色不靠聊天消息通信，而是读写同一个 `AgentState`：分析师写报告字段，辩论写 `investment_debate_state`，经理写最终决策。下游只拿到上游的成品，拿不到上游的工具调用过程。

## 今天学了什么

**1. 判断是不是 agent，看控制流。**项目号称有 12 个 agent，拿两个对比：

```python
# Market Analyst：子图 agent ⇄ tools，最多 20 轮
result = (prompt | llm.bind_tools(TOOLS)).invoke(messages)
if result.tool_calls:   # 去执行工具，结果追加进 messages，再回到 agent
else:                   # 结束，result.content 就是报告

# Bull Researcher
response = llm.invoke(prompt)   # prompt 里塞好了所有报告和辩论历史
```

Market Analyst 每拿到一次工具结果，都由模型决定是再调工具还是写报告，所以是 agent；Bull Researcher 调用一次就结束，只是一次 LLM 调用。

> **原则：** 下一步做什么、什么时候停，由模型决定的才是 agent。

**2. ReAct 循环的真实流程。**

```text
模型输出: "调用 get_stock_data(2026-01-01, 2026-01-09)"   ← 模型决定
代码执行工具，把结果追加进 messages                         ← 代码只负责执行
模型再看一遍全部历史: 再调一个工具？还是写报告？         ← 模型又决定一次
```

**3. 辩论是状态机，不是 agent。**Bull 和 Bear 来回辩好几轮，但决定下一个是谁发言的是 `graph/conditional_logic.py`：

```python
def should_continue_debate(self, state):
    if state["investment_debate_state"]["count"] >= 2 * self.max_debate_rounds:
        return "Research Manager"
    if state["investment_debate_state"]["current_response"].startswith("Bull"):
        return "Bear Researcher"
    return "Bull Researcher"
```

数一下次数、看一眼上一个是谁发言，就决定下一步，全程没有模型参与。LangGraph 的图本质上就是状态机：节点读写 state，条件边根据 state 决定往哪走。

**4. 自主权该放在哪。**整个项目外层是一条固定流程（workflow），只有几个分析师内部是真正的 agent 循环。作者只在很少的地方把决定权交给模型。

> **原则：** 需要判断力的决策交给模型，事先能写成规则的决策交给代码。

## 我理解对的

- **Market Analyst 是 agent，Bull Researcher 不是。**判断对了，而且抓住了关键差别：前者是一个循环，模型拿到结果后还要接着决定；后者调用一次就返回。
- **辩论是状态机。**这是我自己从代码里读出来的：路由函数只看 `count` 和 `current_response` 两个字段，没有模型参与。
- **不让 LLM 来调度辩论，是因为它可能判断错，比如让 Bull 连说两次。**理由成立，讲的是可靠性。另外还有两个理由：一是**成本可预测**，辩论 1 轮就是正好 2 次调用，不会因为模型觉得"还没辩够"一直跑；二是**这一步不需要判断力**，轮流发言、到次数就停，交给模型只会多出不确定性，没有任何收益。

## 我理解错的、不够准的

> **修正：** ReAct 里没有单独的"反思"步骤。我说模型会"去反思、去执行、去思考"，但实际流程只有"模型决定 → 代码执行 → 模型再决定"。所谓"思考"，只是模型在做决定之前生成的一段文字，不是一个独立的环节。

我容易混淆，是因为确实存在显式"反思"的设计（比如 Reflexion 一类的做法），但那是专门另加的一次模型调用。这个项目里就有一个：`memory/reflection.py` 在一笔决策结算之后，单独调用一次模型，对照真实收益写 2~4 句教训，供下次参考。所以**反思是需要专门设计的组件，不是 ReAct 自带的**。

> **修正：** "绑定了工具"不等于 agent。我第一次的判断依据是"大语言模型里绑定了一堆工具，然后有一个循环"，但工具不是关键。Sentiment Analyst 也从三个数据源（新闻、StockTwits、Reddit）拿数据，可调用顺序是代码写死的，模型只负责读完写报告，所以它不是 agent。判断的依据始终是：谁决定下一步。

## 举一反三

**1. 用"谁决定下一步"去看别的系统。**

| 系统 | 谁决定下一步 | 算不算 agent |
| --- | --- | --- |
| Claude Code、Cursor 这类编程助手改代码 | 模型决定读哪个文件、改哪里、跑什么命令、什么时候停 | 是 |
| 一次调用的 RAG 问答 | 检索步骤是代码写死的，模型只负责回答 | 不是 |
| 先用 LLM 判断用户意图，再进入对应的固定流程 | 模型只决定一次走向，之后全是固定流程 | 不算，是 workflow 里的一个 LLM 路由节点 |
| TradingAgents 的多空辩论 | 代码按次数轮流 | 不是，是状态机 |

> **举一反三：** "agent"不是一个角色名，而是一种控制方式。一个系统叫不叫 agent 不重要，重要的是在哪几步把决定权交给了模型。

**2. 自主权是一个光谱，不是二选一。**从左到右大致是：单次调用 → 固定链条（上一步的输出喂给下一步）→ LLM 路由（模型选分支）→ 有上限的 agent 循环 → 开放式 agent。越往右越灵活，也越难预测、越贵。这个项目大部分在左边，只在分析师上用了"有上限的循环"。

**3. "判断力给模型、规则给代码"在别处的样子。**比如做一个客服 agent："这句话是不是在要求退款"交给模型判断；"超过 500 元要人工审批"写在代码里。agent 的最大轮数、超时、重试次数也属于"规则"，应该由代码兜底，对应项目里的 `max_tool_rounds = 20` 和到达上限后的 `wrap_up`。

> **举一反三：** 以后看到一个 agent 框架，先问三个问题：循环在哪？谁决定继续还是停？最坏情况下它会跑多少步、花多少钱？

## 还没解决的问题

> **思考题：** 同样是分析师，为什么 Market Analyst 可以自己决定调用哪些工具，Sentiment Analyst 却是由代码先把数据抓好？提示：Market Analyst 的 prompt 列了 12 个技术指标，要求它从中挑出最多 8 个。

## 下一步

- 先回答上面那道思考题。
- 用一个 ticker 完整跑一遍，对照 `full_states_log_*.json`，看 state 在每个节点之后怎么变化。
- 再看记忆闭环：`memory/log.py`、`memory/settlement.py`、`memory/reflection.py`。
