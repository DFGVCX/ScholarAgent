# Skill: 文献综述自动化生成 (SurveyForge Core)

## 1. 能力描述 (Capabilities)
本 Skill 专门用于处理海量文献（100-1500篇）并生成结构严密、引用真实的学术综述。具备大纲自动合成、章节并行写作、引用真实性校验及学术格式标准化能力。

## 2. 渐进式披露逻辑 (Progressive Disclosure)
Agent 应遵循以下阶段引导用户，而非一次性执行：
- **阶段 1：领域确认与数据评估**。确认 Topic 后，调用 MCP 工具评估相关文献密度，向用户展示初步检索结果。
- **阶段 2：大纲预览与人工干预**。仅输出合成后的全局大纲，等待用户确认或修改章节结构。
- **阶段 3：章节试写与质量反思**。完成第一个章节的生成，并展示初步的“引用校验报告”，通过后才开启全篇写作。
- **阶段 4：引用审计与格式化**。展示 CiteAdapt 处理后的标准化引用列表，进行最终交付。

## 3. 操作规程 (Standard Operating Procedures)
1. **分块处理 (Chunking)**：必须调用 `tools.processor` 对海量摘要进行 Token 分块。
2. **大纲合成 (Synthesizing)**：调用 `tools.synthesizer` 递归合并局部大纲，确保全局逻辑严密。
3. **引用校验 (Citation Guard)**：生成的每一章节必须通过 `tools.citation` 进行指纹比对。
4. **格式化 (Formatting)**：最后步骤必须调用 `tools.formatter` (CiteAdapt) 进行引用格式标准化。

## 4. 质量评估准则 (Acceptance Criteria)
- **引用真实性**：虚假引用率为 0。
- **结构完整性**：涵盖大纲定义的所有核心 Section。
- **衔接度**：通过 LCE 工具处理，章节间过渡自然。
- **格式合规性**：符合用户指定的引用风格 (IEEE/APA/GB)。
