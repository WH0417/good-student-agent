# 好学生 Agent 的 GitHub 项目复用评估

版本：1.0  
日期：2026 年 11 月 18 日  
依据：已确认的产品设计、本地拉取的上游源码、上游单元测试与合成资料验证。

## 1 复用结论

可以复用 GitHub 上已有项目。建议优先复用 `universal-examprep-skill` 的 Flash 版底层模块，并参考 `lecture-to-notes` 的录音处理机制；统一主技能、重点证据与双模式按本项目设计实现。

复用价值主要在文件解析、资料引用、检索、题目答案配对和学习状态落盘。这些模块已有可阅读实现和测试基础。上游完整教学流程与本项目的重点来源、复习目标和增量资料要求存在差异，需要经过适配才能成为本产品的一部分。

## 2 已下载项目与版本

| 项目 | 本地相对目录 | 固定提交 | 读取到的版本 |
| --- | --- | --- | --- |
| [universal-examprep-skill](https://github.com/ZeKaiNie/universal-examprep-skill) | `references/universal-examprep-skill` | `e01d2f7647b0917172ae8ad5fb788b404e977cae` | Flash 5.3 |
| [lecture-to-notes](https://github.com/drpwchen/lecture-to-notes) | `references/lecture-to-notes` | `6f58fe20d945104916301a48c8b37fa7e5ee44f3` | 0.11.1 |

两个仓库均保留独立 Git 记录。来源和提交已写入 `references/upstream-lock.json`；本轮验证后，两者 `git status --short` 均为空。后续评估和改造以这些提交为起点，避免上游变动导致依据不明确。

两个上游代码仓库均附 MIT 许可证；纳入代码时保留原版权声明和许可证。上游示例课程资料和第三方依赖按各自许可证处理。依据为各仓库的 [学习项目许可证](https://github.com/ZeKaiNie/universal-examprep-skill/blob/e01d2f7647b0917172ae8ad5fb788b404e977cae/LICENSE) 与 [录音项目许可证](https://github.com/drpwchen/lecture-to-notes/blob/6f58fe20d945104916301a48c8b37fa7e5ee44f3/LICENSE)。

## 3 可复用的源码模块

### 3.1 学习项目

以下路径相对于 `references/universal-examprep-skill/skills/universal-exam-cram-coach`。

| 源码 | 可复用部分 | 接入要求 |
| --- | --- | --- |
| `coach/extract.py` | PPTX、DOCX、文本等读取，来源文件和页号保留；PDF 可选后端 | 包装成统一资料适配器；图表与公式不能仅以文本解析结果作为已理解证据 |
| `coach/index.py` | 资料切片、BM25 检索和返回出处 | 每次检索绑定明确课程空间 |
| `coach/questions.py` | 题目识别、题目与答案文件配对、知识章节关联 | 更换稳定题目编号，调整数值判分约定，保留原题与生成题区别 |
| `coach/figures.py` | 提取嵌入图片、PDF 图像裁剪等辅助能力 | PDF 部分尚需在配置相应依赖后验证 |
| `coach/state.py` | JSON 状态、笔记视图、单文件原子写 | 增加课程目标、重点证据、提示与答案查看记录；补同科写入控制 |

这些模块值得纳入第一轮开发候选。正式整合时先确定对外数据结构，再取用需要的模块，避免让上游 CLI 的默认规则控制产品行为。

源码依据：[解析模块](https://github.com/ZeKaiNie/universal-examprep-skill/blob/e01d2f7647b0917172ae8ad5fb788b404e977cae/skills/universal-exam-cram-coach/coach/extract.py)、[检索模块](https://github.com/ZeKaiNie/universal-examprep-skill/blob/e01d2f7647b0917172ae8ad5fb788b404e977cae/skills/universal-exam-cram-coach/coach/index.py)、[题目模块](https://github.com/ZeKaiNie/universal-examprep-skill/blob/e01d2f7647b0917172ae8ad5fb788b404e977cae/skills/universal-exam-cram-coach/coach/questions.py)、[状态模块](https://github.com/ZeKaiNie/universal-examprep-skill/blob/e01d2f7647b0917172ae8ad5fb788b404e977cae/skills/universal-exam-cram-coach/coach/state.py)。

### 3.2 录音项目

`lecture-to-notes` 的 `scripts/import_transcript.py` 能导入 SRT、VTT、JSON 等带时间信息的转写，统一为 `start/end/text` 片段并记录来源。已用合成 SRT 验证此能力，适合参考时间戳转换与可追溯性实现。

该导入器不直接接收普通豆包文本纪要。本地验证中，普通 TXT 纪要返回退出码 2，未生成转写结果。因此首版豆包纯文本应走自己的文本入口，以段落定位；不能为满足上游时间戳格式而补造时间信息。

`scripts/transcribe_video.py` 可作为后续录音接入的参考。完整项目还包含视频抽帧、OCR、视觉模型和 Obsidian 等流程，其全量依赖与首版纯文本路径并不相同。首阶段先完成文本导入，需要原始录音处理时再接转写适配器。

源码依据：[转写导入器](https://github.com/drpwchen/lecture-to-notes/blob/6f58fe20d945104916301a48c8b37fa7e5ee44f3/scripts/import_transcript.py)、[音频转写脚本](https://github.com/drpwchen/lecture-to-notes/blob/6f58fe20d945104916301a48c8b37fa7e5ee44f3/scripts/transcribe_video.py)、[依赖清单](https://github.com/drpwchen/lecture-to-notes/blob/6f58fe20d945104916301a48c8b37fa7e5ee44f3/requirements.txt)。

## 4 必须适配的规则与问题

### 4.1 增量导入后的错题关联

已在本地复现：原有题目编号为 `q001`，记录错题后，新增一个排序靠前的题目文件并再次执行 `setup`。旧题变为 `q002`，新题占用 `q001`，而原错题记录仍保存 `q001`。结果是旧错题关联到新题。

原因是 `questions.py` 按当前扫描顺序重新生成题号，`cli.py` 重建题库时保留原有历史记录。我们的产品需要持续导入一学期材料，必须先实现稳定来源编号和题目编号，再复用题库与错题数据。发生内容修改时也要显式迁移或标记旧记录，而非复用无关题号。

### 4.2 数值判分容差

已在本地复现：参考答案为 `2`，用户回答 `2.4`，上游 `grade_answer` 返回 `right`。其数值容差包含按参考答案小数位计算的范围，整数答案可能获得 ±0.5 容差。

应根据题目区分精确整数、近似计算和允许误差。未给出数值误差约定时，不能把该默认行为直接用于统计卷面目标或判定知识掌握。

### 4.3 重点来源与模式控制

上游包含按真题题目数量选择推荐章节的逻辑，技能也会根据剩余天数压缩教学。这些规则不等同于本项目的正式重点认定，也不等同于用户主动选择高分或速通模式。

产品应由新的主技能读取重点证据库和用户模式。真题用于练习材料选择，不据此自动认定正式重点；临近考试不自动改变模式。

### 4.4 多科隔离与掌握记录

上游在未显式指定工作区时可能回退到最后使用的课程。产品的每次操作都应通过课程编号指定确定路径；课程不存在时直接提示，不回退到其他课程。

上游还使用“至少答对一道材料题”作为章节验证条件，没有完整区分是否看过答案。我们的实现要记录独立作答、提示使用和答案查看，将“练习通过”与更广泛的掌握结论区分开。

上述逻辑位于 [CLI 源码](https://github.com/ZeKaiNie/universal-examprep-skill/blob/e01d2f7647b0917172ae8ad5fb788b404e977cae/skills/universal-exam-cram-coach/coach/cli.py) 和 [主技能](https://github.com/ZeKaiNie/universal-examprep-skill/blob/e01d2f7647b0917172ae8ad5fb788b404e977cae/skills/universal-exam-cram-coach/SKILL.md)。

## 5 本轮验证结果与限制

环境为 Windows、Python 3.14.7，验证过程没有安装额外依赖、下载模型或修改上游业务代码。

| 验证 | 结果 |
| --- | --- |
| 学习项目现有单元测试 | 运行 77 项，73 项通过，4 项跳过，无失败 |
| 跳过原因 | 缺少 pypdf 与 pypdfium2，对应 PDF 文本或图像测试 |
| 内置中文样例流程 | 识别 3 章、12 道题，完成错题记录、状态读取、笔记与复习摘要导出 |
| 增量题目编号 | 复现旧错题关联到新增题目的问题 |
| 精确整数判分 | 复现答案 2、作答 2.4 被判对的行为 |
| SRT 转写导入 | 成功保存 2 段中文文本及原有起止时间 |
| 普通 TXT 纪要导入上游时间戳导入器 | 返回 2，未生成转写；需单独文本入口 |

单元测试成功仅说明上游已有检查在当前环境中的结果，不表示它满足本项目验收要求。尚未运行真实课程的模型教学、真实录音转写、OCR 或视觉理解，也没有验证 PDF 可选后端。

验证脚本和结果位于：

- `audit/reuse_smoke.py`
- `audit/reuse-smoke-result.json`
- `audit/universal-examprep-unittest.log`
- `audit/runs/` 下对应运行目录中的 `commands.json` 和 `import-log.txt`

## 6 后续实施安排

1. 保留两个上游参考仓库和固定提交；抽取或适配模块时记录来源及许可证。
2. 实现我们的课程编号、稳定来源及题目编号、重点证据数据结构和显式课程路由。
3. 完成豆包文本导入、逐课摘要、用户指定重点和全科重点导出，先验证产品最关键的闭环。
4. 接入已有资料解析、检索和题目配对能力，再完成高分与速通的教学编排、简单变式和错题恢复。
5. 配置录音转写适配器，按设计文档的验收标准检查端到端流程。

当前完成的是源码复用评估和验证准备。项目主体功能尚未开始整合，后续代码以已确认设计和上述适配项为依据。
