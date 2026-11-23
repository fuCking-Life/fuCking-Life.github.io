覆盖范围：learn-claude-code s02（Tool Use）。今天聊了三件事：bash 本来就能读写文件，s02 为什么还要加专用工具；`edit_file` 比 `sed` 好在哪；用带 trace 的 s02 跑两个小任务，看模型一圈圈在干什么。

今天最大的收获是：**新工具没有给 agent 增加能力，改善的是接口**：模型不用再把意图翻译成 shell 语法，harness 也看得懂它要动哪个文件。读 trace 时我还看到，模型只知道 context 里有的东西，进一个陌生环境要先花好几圈去摸。我一开始只说出了"拆分工具让模型选得更准"这层表面，预测也漏了一大半工具调用；还有一道题没答完。

<!--more-->

## 今天学了什么

**1. s02 只改了工具这一层，循环一行没动。**新加 `read_file`、`write_file`、`edit_file`、`glob` 四个文件工具，执行从写死的 `run_bash(...)` 换成查表（`s02_tool_use/code.py`）：

```python
TOOL_HANDLERS = {"bash": run_bash, "read_file": run_read, "write_file": run_write,
                 "edit_file": run_edit, "glob": run_glob}

handler = TOOL_HANDLERS.get(block.name)
output = handler(**block.input) if handler else f"Unknown: {block.name}"
```

| 工具 | 参数 | 返回给模型 |
| --- | --- | --- |
| `bash` | `command` | stdout + stderr 拼成一串，不受工作目录限制 |
| `read_file` | `path`，可选 `limit` | 文件内容，或 `Error: ...` |
| `write_file` | `path`、`content` | `Wrote N bytes to ...`（整个文件覆盖写入） |
| `edit_file` | `path`、`old_text`、`new_text` | `Edited ...`，找不到返回 `Error: text not found` |
| `glob` | `pattern` | 匹配的路径列表，或 `(no matches)` |

四个文件工具都要过 `safe_path`，路径跑出工作目录就拒绝。

**2. 新工具改善的是接口，从两边看。**

- **模型这边，省掉一层"翻译"。**模型想的是"创建 count.py，内容是这几行"，用 bash 就得改写成某种 shell 的语法：Mac 上写 heredoc；Windows 的 cmd.exe 里 `(` `)` `>` `&` 都要用 `^` 转义，漏一个，内容就错了。换成 `write_file(path, content)`，直接填两个 JSON 参数，不经过 shell。前几天模型在 macOS 上写出不支持的 `cat -A`，就是一次翻译出错：想做的事是对的，命令却说错了。
- **harness 这边，从黑盒变成看得懂的操作。**bash 收到的只是一串字符串，像 `python -c "..."` 这种，harness 根本看不出它要动哪个文件。`write_file(path="a.py", ...)` 是具名操作加路径参数，所以才能用 `safe_path` 限制在工作目录，后面 s03 才能按工具和路径放行，s04 才能统一记录。

> **原则：** 专用工具不加能力，加的是可控性：模型少犯翻译错误，harness 看得懂操作。

**3. `edit_file` 比 `sed` 少操心什么。**

```bash
sed -i '' 's/^旧的一行$/新的一行/' README.md
```

这一行里，跟"把这段文字换成那段"无关的东西有：正则（`^` `$` 是锚点，原文里的 `.` `*` `[` `/` 都要转义）、`s/旧/新/` 的分隔符、平台差异（macOS 要写 `sed -i ''`，Linux 写 `sed -i`，Windows 的 cmd 里根本没有 sed）。还有一点更要紧：sed 没找到要替换的内容时什么都不做，退出码却是 0，看起来像成功了；`edit_file` 会明确返回 `Error: text not found`。

> **要点：** 专用工具更容易把失败如实报告出来。

**4. 任务 A 的 trace：10 圈，第 7 圈才开始写。**任务是"写一个 count.py，统计 places.csv 里每种地点类型各有多少个，运行它，把结果告诉我"，在 Windows 上跑：

| 圈 | 工具 | 在弄清什么 |
| --- | --- | --- |
| 1 | `glob("**/places.csv")` 和 `bash dir`，同一次回复 | 文件在不在、目录里有什么 |
| 2 | `read_file` ×2 | 文件格式：没有表头，三列是城市、类型、设施 |
| 3 | `bash`：查 python 版本 + 看原始字节 | 返回 `(no output)` |
| 4–6 | `bash`：`python --version`、`where python`…… | 找 python：PATH 上的 `python` 是应用商店的占位程序，什么都不输出 |
| 7 | `write_file count.py` | 写脚本 |
| 8 | `bash`：运行 | 三种类型各 1 个 |
| 9 | `bash`：再读一遍原始字节 | 结果全是 1，核对一遍 |
| 10 | 不调工具 | 汇报结果，并说明"没有表头，第一行也算数据" |

同一个任务，s01 在 Mac 上跑过 3～6 圈。这次多出来的圈数，大多花在摸清环境上。

**5. 一次回复里可以有多个 tool_use。**第 1 圈的 `glob` 和 `dir` 是模型在同一次回复里一起发出的，发 `dir` 的时候它还没看到 `glob` 的结果。harness 按顺序逐个执行，两个结果放进同一条消息送回。所以这两个调用没有先后因果，只是两个独立的"先看一眼"，省掉一圈。

**6. 模型只知道 context 里有的东西。**文件在哪、CSV 有没有表头、python 在哪，模型全得靠工具去查。我预测时觉得 `dir` 多余，是因为我脑子里早就知道这些。找 python 那 3 圈，是这台电脑的环境专门"收的税"。

**7. 新发现：模型看到的是乱码。**cmd.exe 输出的中文是 GBK 编码，`run_bash` 却按 UTF-8 解码（`errors="replace"`），解不了的字就变成了 `������`。这次文件名都是英文，没耽误事；文件名要是中文，模型就"看不清"了。

## 我理解对的

- **把 bash 拆成专用工具、每个配一段描述，模型更容易选对。**对，但只是表层。漏了 README 的两个重点："省掉翻译"和"harness 看得懂"。
- **`edit_file` 不用拼 shell 命令**，给出路径、原文和新文字，剩下的由 harness 里的 Python 去做。
- **预测任务 A 会用 `write_file` 写 count.py、用 bash 运行**，都说中了。
- **前 6 圈在弄清三件事**：文件在哪、内容什么格式、python 在哪；找 python 是这台 Windows 电脑才多出来的。

## 我理解错的、不够准的

> **修正：** 我一开始没看懂"省掉一层翻译"。其实就是：模型想做的事，要先改写成某种 shell 的语法，才能交给 bash。

帮我想通的比方：s01 像托一个只懂外语的快递员送东西，"送到哪、送什么"都得先翻译成他的语言，翻错一个词就送错；s02 像一张印好栏目的快递单，地址填一栏，物品填一栏。

> **修正：** 我把 sed 的麻烦归到"Windows 的反斜杠"。其实麻烦在 sed 自己：正则、分隔符和平台差异；我还漏了"失败时会不会说出来"这一点。

> **修正：** 我预测的工具调用漏了一大半：先探索、一次同时调多个工具、找 python、改完再验证；任务 B 还漏了 `glob`，而任务里明明写着"找出所有 .md 文件"。我以为原因是"用人类思维去想，而模型是一步步推"。更准确的说法是信息差：我已经知道文件内容和 python 在哪，模型得自己去查。

一个工程师第一次进陌生目录，其实也会先 `ls` 看一圈。

> **修正：** 我以为第 1 圈是"先 glob，再 dir"，有先后关系。其实两者是同一次回复里并行发出的。

> **修正：** 我说"Mac 自带 Python，不用找"。新版 macOS 早就不带 `python` 了，只有 `python3`，还得先装命令行工具。不过判断方向对：在 Mac 上，通常不会有找 python 那 3 圈。

## 举一反三

**1. "专用工具 + 万能 shell"是常见组合。**

| 系统 | 专用工具 | 万能工具 | 权限能按什么管 |
| --- | --- | --- | --- |
| learn-claude-code s02 | `read_file`、`write_file`、`edit_file`、`glob` | `bash` | 文件工具按路径（`safe_path`）；bash 只有黑名单 |
| Claude Code | Read、Write、Edit、Glob、Grep | Bash | 专用工具可以按路径和操作批准 |

万能工具灵活，但 harness 看不透；专用工具受限，但可控、出错时会说出来。

**2. 环境税可以靠"写清环境"来减少。**把 python 在哪、用什么 shell 写进 system，或者 CLAUDE.md、AGENTS.md 这类给 agent 看的文档，模型就不用每次花几圈去摸。s02 在 Windows 上的 system 只说了"用 cmd.exe 语法"，没说 python 在哪。

> **举一反三：** 读一个 agent 的 trace 时，先把每一圈归类：是在干活，还是在摸环境、在验证？摸环境花的圈数，往往能靠文档省掉；工具返回的东西（包括编码）模型看到多少，它就只能推理多少。

## 还没解决的问题

> **思考题：** 任务 B 是"找出这个目录里所有的 .md 文件，把 README.md 的第一行标题改成'沙盒说明'"。README 原本只有一句普通的话，不是标题。模型用 `edit_file` 把这句话整句替换成了 `# 沙盒说明`，原来那句话没了。这是想要的结果吗？如果不是，问题出在哪一环：任务说得不清楚、模型该先问一句，还是工具的设计？提示：还没给。

## 下一步

- s02 第 4 步：把某个工具的描述改模糊或写错，看模型会不会选错工具，对比改前改后。
- 在 Mac 上再跑一遍。Mac 上 s02 的 system 没有 Windows 那句"优先用专用文件工具"，结果可能不一样。
