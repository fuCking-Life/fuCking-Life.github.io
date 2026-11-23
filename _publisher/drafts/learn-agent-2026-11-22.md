覆盖范围：复习（learn-claude-code 前两天的内容）+ TradingAgents v0.7.1 的分析师循环，和 learn-claude-code s01 的最小 agent 循环对照。今天聊了四件事：复习时暴露的误解、模型能力怎么推动 prompt → context → harness 往外走、LangGraph 画出来的循环和 s01 有什么不同、多个 agent 之间怎么交接。

今天最大的收获是：**去掉 LangGraph 这层外衣，TradingAgents 的分析师就是 s01 那个 ReAct 循环**，连到上限时的收尾做法，都和我前一天自己设计的方案基本一致。复习 8 道回忆题只对了 1 道，暴露出好几处没记牢的细节；对照题里有两处看漏：以为收尾只靠提示词禁止调工具，以为"没问用户"是没做。

<!--more-->

## 今天学了什么

**1. 推动外层进步的是两股力量。**复习时我追问：模型能力是怎么推着 prompt、context、harness 一层层往外走的？只是"把能力内化进模型"吗？

- **内化**：里层的技巧被训练进模型。"请一步步思考"变成了推理模型自带的思考，"请按 JSON 输出"变成了 function calling；
- **任务变大**：模型变强以后，人们敢交给它更长、要真动手的任务，于是撞上训练消除不了的结构性限制：上下文窗口有限、看不到没交给它的东西、权限要在工具层硬拦、评不好自己的活。

> **原则：** 模型变强不会造出 harness，它只是让任务跑得够远，撞上了 harness 这面墙。

所以"请一步步思考"最可能被模型吃掉；"把 stderr 返回给模型""禁止删除测试目录以外的文件"会一直留在 harness 里。

**2. 看一次真实的出错工具结果。**让 Claude Code 跑了一条必定报错的 `ls`，从会话日志里摘出它送回模型的结果：

```json
{"type": "tool_result",
 "content": "Exit code 2\nls: cannot access '/no-such-dir-for-review': No such file or directory",
 "is_error": true,
 "tool_use_id": "toolu_..."}
```

比 s01 的 `run_bash` 多了两样：content 开头的退出码，和 `is_error` 出错标记。`type` 和 `tool_use_id` 是 API 规定的，s01 也有。

> **要点：** 好在哪不在"信息更多"，而在"成功还是失败"由 harness 直接告诉模型，不用模型读文字去猜。

**3. LangGraph 的节点和边，就是 s01 的 `while True`。**节点是读写共享 state 的函数，条件边由一个判断函数读 state，决定下一个去哪。分析师的子图在 `tradingagents/graph/setup.py` 的 `_analyst_graph` 里：

```python
def _tools_or_done(state):
    return "tools" if state["messages"][-1].tool_calls else END

graph.add_conditional_edges("agent", _tools_or_done, ["tools", END])
graph.add_conditional_edges("tools", more_or_wrap_up, ["agent", "wrap_up"])
graph.add_edge("wrap_up", END)
```

`agent → tools → agent` 连成一个圈，就是循环。LangGraph 本身既不是 agent 也不是 workflow，它只是画控制流的工具。

> **原则：** 是 agent 还是 workflow，看条件边的判断函数读的是什么：读模型回复里有没有 `tool_calls`（`_tools_or_done`），这一层是 agent；读计数器（辩论的 `should_continue_debate`），这一层是 workflow。

**4. 用三个问题对照 s01。**

| | s01 | TradingAgents 分析师 |
| --- | --- | --- |
| 在哪转 | Python 的 `while True` | 图里 `tools → agent` 那条边 |
| 谁喊停 | 模型不再调工具 | 同上，外加 `max_tool_rounds = 20` 的护栏 |
| 最坏多贵 | 没有上限 | 一个分析师最多 20 + 1 = 21 次模型调用，一圈还可以同时调多个工具，而且有好几个分析师 |

**5. 到上限时的收尾。**`agents/analysts/turn.py` 里，用满 20 圈后会追加一条 `WRAP_UP` 消息，再调一次模型：

```python
WRAP_UP = ("You have used every tool round this report allows. Write your final report "
           "now from the tool results above, and say which data you could not retrieve.")

prompt = prompt.partial(tool_names="none; your tool rounds are spent")
result = (prompt | llm).invoke(_as_text(messages))   # llm，不是 llm.bind_tools(tools)
```

和我前一天在 s01 上自己设计的收尾方案逐条对照：

| 我的方案 | TradingAgents |
| --- | --- |
| harness 告诉模型为什么停 | `WRAP_UP` 第一句 |
| 再调一次，结构上禁止调工具 | 调用 `llm` 而不是 `llm.bind_tools(tools)`，请求里根本不挂工具；再加 `wrap_up → END` 这条边，双保险 |
| 说明做到哪、还差什么 | "say which data you could not retrieve" |
| 问用户要不要继续；无人值守时要有默认策略 | 无人值守：带着缺口写完报告，流程继续往下走 |

还有两个我没想到的工程细节：提示词里的工具名同步改成 `none`，让提示词和结构一致（不然模型可能还去调，有的服务商这时会返回空回答）；`_as_text` 把历史里的工具调用改写成普通文字，因为有的服务商见到历史里有工具调用、这次请求却没挂工具，会直接拒绝。

> **要点：** 结构上禁止是对的，但要付兼容成本，而且提示词要和结构说同一件事。

**6. TradingAgents 不是"主 agent 带 sub-agents"。**没有哪个模型决定"派谁去干什么"，先后顺序是 `add_edge` 写死的：四个分析师从 `START` 并行出发，跑完进入多空辩论。只有带工具的分析师是 agent，研究员、交易员都只调一次模型。角色之间读写同一个 `AgentState`，像一块公共黑板：分析师只往自己的格子（`market_report` 等）写一份报告，它那 20 圈的工具调用留在私有的 messages 里，跑完就丢。

## 我理解对的

- **底下是同一个 ReAct 循环**，框架只是换了画法。
- **谁喊停**：平时是模型，20 圈是 harness 的护栏；护栏不改变主控制流的归属，分析师仍是 agent。
- **哪些会被模型吃掉**：三条判断都对。"一步步思考"会被吃掉，返回 stderr 是 Observation，禁止越界删除是 Permission。
- **为什么收尾要用结构禁止**：提示词只是请求，结构才是保证。
- **共用 messages 会让信息密度极低**，上游只该把结论交给下游。
- **只交报告的代价**：下游对报告存疑时，要么自己再调一遍去核实（浪费 token），要么将错就错。分析是对的，当时只是不知道 TradingAgents 的下游没有工具，只剩"将错就错"这一条。

## 我理解错的、不够准的

> **修正：** 我说 context 是"让模型看得更多"。其实是决定每一步给模型看什么，越精准越好，给太多反而把关键信息淹没。

"上下文"听起来像"背景越多越好"，但模型每一步的注意力是有限的。

> **修正：** 我以为 tools 放在 messages 里，assistant 消息里装的是"执行工具的代码"。其实一次请求里 `system`、`tools`、`messages` 三样并列；`tool_use` 只写工具名和参数，真正执行的是 harness，结果以 user 角色送回，靠 `tool_use_id` 配对。

在 API 层面，模型只负责"开单子"。

> **修正：** 我把到上限时的"让模型写总结"和"`tool_choice: none`"当成二选一。其实是同一个方案的两部分：结构只保证"不能再调工具"，总结仍由模型写，而且 harness 还得告诉模型为什么停，因为模型自己不知道有上限。

> **修正：** 我说权限"不可能内化到模型里"。太绝对了：安全训练能让模型更谨慎，但倾向不等于保证，一次 prompt injection 就可能把它带偏。谨慎可以内化，保证只能放在工具层。

> **修正：** 我觉得"让模型自己申请延长上限"没什么风险。其实风险不小：模型评不好自己的进度，总觉得快好了；用户被问多了会习惯性点同意；无人值守时没人可问；模型也看不到自己的 token 消耗，要 harness 交给它。

> **修正：** 读 TradingAgents 的收尾代码时，我以为它只是在提示词里写了"别调工具"。其实它调用的是没挂工具的 `llm`，从结构上就调不了，提示词那句是为了和结构保持一致。

关键就在 `llm` 和 `llm.bind_tools(tools)` 的区别，我没注意这一行。

> **修正：** 我以为它"没问用户要不要继续"是没做。其实我自己的方案里就写了"无人值守时要有默认策略"：TradingAgents 跑的时候没人在等，回测可能一口气跑几百个日期，停下来问一句，整条流水线就卡住了。

> **修正：** 复习 s01 的停止实验时，我说"到第 3 圈停了，用户看到最后一条输出"。其实用户拿到的是空回答：原版只打印最后一条消息里的 text，而最后一条是工具结果，里面没有 text。活其实干完了，模型却永远没看到结果。

## 举一反三

**1. 交接时只交结论，是多 agent 系统的通用做法，也有通用的代价。**

| 系统 | 上游交给下游的 | 下游能核实吗 |
| --- | --- | --- |
| TradingAgents | `AgentState` 里的一段报告 | 不能，下游没有工具 |
| 编程助手派子 agent 查资料 | 子 agent 的最终回复 | 主 agent 有工具，可以回头查，但会重复花钱 |
| 客服系统的意图路由 | 一个意图标签 | 下游通常直接信 |

缓解的办法：报告里带关键数字和来源；原始工具结果存档（TradingAgents 有 `full_states_log`），留给人或独立评估者查；对关键数字做确定性检查。

**2. 看 LangGraph、CrewAI 这类框架，先问"判断函数读的是什么"。**框架画出来的图，有的边由模型决定（agent），有的边由代码决定（workflow），一个项目里往往两种都有。

> **举一反三：** 设计交接时先问：下游拿到的是结论还是证据？上游错了，下游有没有办法发现？没有证据时，下游往往连"存疑"都不会，一个看起来合理的错数会被直接当成事实。

## 还没解决的问题

> **思考题：** 上游 agent 把结果交给下游时，交接的数据该怎么设计：只交一段报告、报告里附证据（关键数字、来源），还是允许下游按需回查原始工具结果？各自的成本和风险是什么？提示：等学到多 agent 那几课时再展开。

## 下一步

- 当晚接着做了 learn-claude-code s02（加工具），写在下一篇。
