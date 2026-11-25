# Agent 内部工具

本文件只供 Agent 执行，不要求用户阅读或输入这些命令。下面 `student.py` 和 `DATA` 均替换为实际绝对路径。命令成功返回 JSON `ok: true`，输入或资料错误返回退出码 2。媒体环境准备见 media.md。

```text
python student.py --data-dir DATA course-create --name 数据结构 --mode pass
python student.py --data-dir DATA course-list
python student.py --data-dir DATA status --course COURSE
python student.py --data-dir DATA mode-set --course COURSE --mode pass --max-score 100 --pass-score 60
python student.py --data-dir DATA import --course COURSE --kind transcript --file TRANSCRIPT --speaker teacher
python student.py --data-dir DATA import --course COURSE --kind audio --file AUDIO
python student.py --data-dir DATA import --course COURSE --kind material --file SCANNED_PDF_OR_IMAGE
python student.py --data-dir DATA media-retry --course COURSE --source SOURCE
python student.py --data-dir DATA import --course COURSE --kind summary --file SUMMARY --lecture LECTURE
python student.py --data-dir DATA source-show --course COURSE --source SOURCE --start 0 --limit 30
python student.py --data-dir DATA summary-set --course COURSE --lecture LECTURE --file NOTES
python student.py --data-dir DATA focus-list --course COURSE
python student.py --data-dir DATA focus-add --course COURSE --title 树的遍历 --note 用户明确指定树的遍历为重点
python student.py --data-dir DATA focus-confirm --course COURSE --candidate CANDIDATE --title 具体知识点 --note 用户确认内容
python student.py --data-dir DATA focus-retract --course COURSE --focus FOCUS --reason 撤回说明及来源
python student.py --data-dir DATA search --course COURSE --query 栈和队列
python student.py --data-dir DATA knowledge --course COURSE
python student.py --data-dir DATA topic-link --course COURSE --topic TOPIC --prerequisite FOUNDATION
python student.py --data-dir DATA plan --course COURSE
python student.py --data-dir DATA scope-set --course COURSE --file USER_CONFIRMED_SCOPE_JSON
python student.py --data-dir DATA progress-mark --course COURSE --topic TOPIC --status explained
python student.py --data-dir DATA practice-list --course COURSE
python student.py --data-dir DATA practice-show --course COURSE --question QUESTION
python student.py --data-dir DATA answer --course COURSE --question QUESTION --response 用户作答
python student.py --data-dir DATA practice-add --course COURSE --file GENERATED_JSON
python student.py --data-dir DATA export --course COURSE
```

`import` 返回来源编号与可用课次编号。资料重复导入不会生成重复重点。`resume` 等同于恢复指定课程的状态。说话人默认未知，不能为绕过候选确认而替用户作声明。

scope-set 接收用户明确给出的章节标题列表，或 include_titles/exclude_titles 等结构化范围对象。只把用户实际指定的范围写进去；录音出现过哪些章节不等于范围。无范围时按已提供课件复习，不要求先补齐录音。

生成题 JSON 的最小示例，编号必须替换为当前课程已有编号：

```json
{
  "question": "一棵二叉树有 7 个度为 2 的结点，叶子结点数是多少？",
  "answer": "8",
  "explanation": "对非空二叉树，叶子结点数等于度为 2 的结点数加一，因此为 8。",
  "reference_ids": ["当前课程原题编号"],
  "topic_ids": [],
  "source_ids": []
}
```

有参考题时可由它推导资料来源。没有参考题时必须提供课程中存在的 `topic_ids` 和可读取的 `source_ids`。`options` 可选，格式为 `["A. ...", "B. ..."]`。程序校验引用与必要字段，题目语义和解答正确性仍由生成它的 Agent 核对。
