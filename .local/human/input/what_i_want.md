YanShi is a skill to call agent-cli

需要能调用 agent-cli 来进行派发任务
需要能兼容支持主流的各种 agent-cli
还要能控制对应 agent-cli 使用的模型，上下文长度，effort 等
还要能监控对应 agent-cli 的执行状态，输出，错误等

主要目标是能使得 sub-agent 这一步变成不只局限在同一工具内
以及通过调用的过程能正确实现监控sub-agent（other agent cli）的运行状态和进展，并且不需要占用大量的上下文进行监控（需要用超轻量模型对sub-agent的执行过程进行监控和总结）

