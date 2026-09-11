# SBR：Skill Behavioral Rules

恶意智能体技能检测方法的纯代码仓库。

**SKILL.md → 行为图 → 局部文本谓词 → 规则联合匹配 → 稀疏加权判定。**

仓库仅包含源码、提取提示词、测试与使用说明，不包含预训练模型、数据集、真实样例文档、参考预测或实验产物。使用者需要提供自己的数据并训练模型。

方法采用原生一元节点谓词、规范结构签名排序；不计算 PCA confidence，不按 PCA 筛选或排序。训练先拟合完整候选矩阵，再对等价联合匹配特征进行无损压缩。

## 安装与测试

使用 Python 3.12：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[test]'
python -m pytest -q
python scripts/verify_release.py
```

Windows 使用 `.venv\Scripts\Activate.ps1` 激活。测试在临时目录中生成人工输入与模型参数，不依赖或下载任何预训练模型和数据集。不同系统或数值库构建可能产生不同的拟合参数。

## 用自己的数据训练

按[输入格式](docs/INPUTS.md)准备图语料和训练/测试划分：

```bash
skill-rule train --manifest /path/to/dataset.json --output runs/trained --workers 4
```

输出目录必须不存在。部署模型保存到 `runs/trained/model`，训练诊断文件保存在同级目录。训练不读取既有模型或测试参考分数。缺图默认报错；仅在复现既有历史缺图约定时使用 `--missing-graphs historical-zero-hits`。

## 检测与评估

新文档提取需要自行配置兼容 OpenAI 的 LLM 服务；认证信息通过环境变量私下提供：

```bash
export SKILL_RULE_LLM_BASE_URL=https://your-service.example/v1
export SKILL_RULE_LLM_MODEL=your-model-name
# 如需认证，设置 SKILL_RULE_LLM_API_KEY。
skill-rule detect /path/to/skill --model runs/trained/model --output runs/detection.json
```

程序只读取 `SKILL.md` 和明确引用的脚本文件名，不读取脚本正文、不访问文档链接、不执行 skill。提取失败或空图会返回错误。输出包含分数、阈值、局部谓词、规则命中、权重和变量绑定，并保存提取后的图。

```bash
skill-rule extract /path/to/skill --output runs/skill.graph.json
skill-rule detect-graph runs/skill.graph.json --model runs/trained/model --output runs/result.json
skill-rule evaluate --manifest /path/to/dataset.json --model runs/trained/model --split test --output runs/test.json
skill-rule compress-model --model runs/trained/model --output runs/compiled
```

检测、评估和模型压缩必须显式传入 `--model`，不会加载内置模型或自动下载模型。Python 接口同样需要路径：`Detector(model_dir)`、`compress_model(output, model_dir)`。固定图检测和训练不需要 LLM 或 GPU。

比较两个本地模型：

```bash
python scripts/compare_bundles.py /path/to/candidate --reference /path/to/reference
```

方法的数学定义及训练边界见 [METHOD.md](docs/METHOD.md)。请将私有数据、模型和运行产物保留在版本控制之外；仓库已忽略常见产物目录与模型文件格式。
