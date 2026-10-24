# Save Live Captions

将 Windows 11「实时字幕」保存为带时间戳的 UTF-8 TXT 文件。程序只提供一个便携版本：**SaveLiveCaptions.exe**，双击即可使用，无需安装程序或 Python。

字幕识别由 Windows 完成；本工具负责读取、整理和保存字幕，不另外运行语音识别模型。

![字幕记录窗口](docs/dashboard-preview.png)

## 使用方法

1. 从 [Releases](https://github.com/lyclycNSP/SaveLiveCaptions/releases) 下载 v1.4.0 或更新版本的 `SaveLiveCaptions.exe`。历史 v1.3.0 的文件名及分发方式不同。
2. 双击运行。程序会检测 Windows 实时字幕，未打开时自动通过 `Win + Ctrl + L` 打开，已打开时不会重复切换。首次使用可能需要在系统字幕窗口完成语言设置；自动打开失败时可手动打开后重试。
3. 等界面显示「准备就绪」，点击「开始录制」，选择保存文件夹，然后播放要记录的内容。
4. 点击「停止并保存」，等待显示「已保存，可以开始下一段」。窗口会保留，可以直接开始下一段录制。
5. 用标题栏关闭按钮或 `Alt + F4` 退出。录制或保存过程中关闭会先确认；取消则继续，确认后等待保存完成再退出。

窗口支持任务栏显示、拖动、缩放和最小化。最小化不影响录制。保存失败会提示错误并保留窗口。

录制期间会请求 Windows 保持屏幕亮起并阻止自动睡眠。停止、读取失败或关闭时释放请求，恢复原有电源行为；不修改系统电源计划。界面会显示「保持亮屏」。这不阻止手动锁屏、合盖或手动睡眠，也不保证这些状态下字幕可用。保持亮屏不能解决字幕历史改写导致的重复，相关处理由下述保存引擎完成。

## 文件保存规则

- 每段生成独立 TXT；同秒开始多段时自动增加序号，不覆盖旧文件。
- 首次选择文件夹后，本次程序运行期间沿用该位置。取消文件夹选择会使用 `~/Documents/captions`。
- 每次开始录制时，把首次读取到的已有字幕作为基线，后续持续排除它及滚动残留。旧尾句继续扩写时只保存新增文字。
- 文件由已滚出的正文和当前字幕草稿组成。窗口内的句子在连续观察后显示到 TXT，但仍可更正；识别结果改写、标点重新断句时更新草稿，只保留该位置的当前版本。
- 停止或字幕窗口断开时，尝试写入尚未稳定的尾句。字幕读取或写入错误会在界面提示。
- 时间戳是程序首次观察到该段文字的时间，**不是音频的原始时间码**。

输出示例：

```text
[14:20:03] 这是本次录制的新字幕。
[14:20:06] 停止保存后，可以直接开始下一段。
```

## 使用限制

需要 Windows 11 的实时字幕功能已可用。程序依赖系统字幕控件的 UI Automation 接口；系统界面结构改变可能影响读取。

建议先开始录制，再播放下一段。新旧内容的分界是连接后第一次成功读取，无法精确对应点击时刻或音频帧。相邻画面按文字及上下文对齐；如果系统恢复已滚出的历史、整窗改写而失去上下文，或相同画面下再次说出同一句话，仍可能难以区分新旧。TXT 无法证明音频是否已完整转录。

最新样本在未熄屏时也出现大块重复，说明熄屏不是重复的必要条件。旧保存方式把历史改写误当新文本，已改用可更新的字幕草稿；尚未确认熄屏时是否另外存在字幕更新或读取延迟，不能视为已经支持黑屏录制。程序不能恢复从未读到的字幕。

不要在录制过程中手动编辑输出文件。当前不提供自动重连、精确音频时间码或字幕文件格式转换。真实长时语音录制和跨显示器 DPI 手动操作仍需实际使用确认。

## 从源码运行

建议使用 Python 3.11 和项目虚拟环境。以下命令在 Windows PowerShell 中执行：

```powershell
git clone https://github.com/lyclycNSP/SaveLiveCaptions.git
cd SaveLiveCaptions
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe src/main.py
```

源码入口与便携 EXE 行为相同，都会自动检测并打开系统实时字幕。

## 构建便携 EXE

使用 PowerShell 7，在仓库根目录执行：

```powershell
.venv/Scripts/python.exe -m pip install pyinstaller
./scripts/build.ps1 -Python .venv/Scripts/python.exe
```

输出为 `dist/SaveLiveCaptions.exe`，只需分发这个文件。图标等运行资源已经包含在 EXE 内。单文件程序会在启动时把依赖解压到临时目录。

`build/` 是可再生成的打包中间产物，`dist/` 是最终输出，均不进入 Git。项目不再构建文件夹版或安装程序。

## 配置与检查

在 [src/function/config.py](src/function/config.py) 中调整以下参数：

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `STABLE_THRESHOLD` | 3 | 同一句连续观察多少次后显示到 TXT 草稿，不代表识别已定稿 |
| `MIN_LENGTH` | 10 | 未完成句子在仍显示时提前写入的最小长度 |
| `SIMILARITY` | 0.85 | 尾句标点变化后继续扩写所需的最低相似度；严格原文追加不受此限制 |

完整短句和停止时保存的尾句不受最小长度过滤。不同数字不通过模糊相似度合并；如果相邻画面的上下文明确对应同一位置，窗口内数字识别的更正也会更新为当前版本。后来独立说出的不同数值仍保留。

保存引擎会对齐相邻画面中未变的句子，保留滚动首句被截短前已读到的完整文字，并忽略临时只暴露原画面前缀的采样。正常新增走追加路径；草稿更正使用临时文件原子替换。只在内存保留当前字幕窗口，不对最终文件做全局去重。

运行测试和静态检查：

```powershell
.venv/Scripts/python.exe -m pip install flake8 mypy
.venv/Scripts/python.exe -B -m unittest discover -s tests -v
.venv/Scripts/python.exe -m mypy src/ --explicit-package-bases --ignore-missing-imports
.venv/Scripts/python.exe -m flake8 src/ --count --select=E9,F63,F7,F82 --show-source --statistics
```

测试包含真实 Tk 窗口及模拟的字幕、自动启动流程，不等于真实语音识别验收。实现边界与修复说明见 [技术说明](docs/code-review.md)。

## 致谢与许可

本项目 fork 自 [LiveCaptionsHelper/SaveLiveCaptions](https://github.com/LiveCaptionsHelper/SaveLiveCaptions)，沿用 [MIT 许可证](LICENSE)。

数字归一化使用了 thodoris 在上游 [PR #22](https://github.com/LiveCaptionsHelper/SaveLiveCaptions/pull/22) 中的 MIT 实现；小数断句修复参考了 [PR #21](https://github.com/LiveCaptionsHelper/SaveLiveCaptions/pull/21) 的问题分析。


