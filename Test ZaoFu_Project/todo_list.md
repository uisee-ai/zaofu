to-do-list：
1. issue从github同步到zaofu后，进入triage queued下一步是triage分析，目前的优先级是按照最新的issue进行triage分析。合理方法应该是按照p0,p1作为优先处理。
2. triage分析阶段，如果能判断以前是否处理过相似问题，再决定要不要修复或者参考以前的问题进行修复，可能会更好。
3. feishu联调部分，特别是gantt图、多维表格的显示/字段设计，feishu群组里的卡片显示设计，需要做的更加清晰舒适美观。
4. triage queued的动画可以去掉
5. 如果用户在github通过new template的方式创建issue,可以参考warp,他们有自己的github issue report template，我们也应该做一个自己的template,但是要做的合理，设计不冗余繁琐，不能让用户填写的时候感到厌烦
6. Triage页面的前端设计：点击某个issue,可以跳转到issue详情页面，而不是在原页面1：1左右两半；可以新建一个标签页或者不新建，直接跳转到issue详情页，按照2：1,左边是issue body,右边是meta data（也就是原始issue详情页中issue body上面的内容）。同时在跳转后的issue详情页，支持另一个issue详情页切换，而不需要先退出当前issue详情页再点击下一个issue,进入下一个issue的详情页。
7. 支持对批量issue进行操作：批量start triage、批量cancel queued triage等等
8. need info往github comments发消息
<!-- 9. setting处指定issue、pr的仓库 -->
10. https://github.com/t8y2/dbx 跑稳：/issue-merge
11. 参考以下产品进行功能/前端优化：https://github.com/warpdotdev/warp https://github.com/multica-ai/multica https://linear.app/ 
12. 测试框架：zaofu自身 产品项目 文件：/Test ZaoFu_Project 因为是两条工作树（/issue-merge功能完善和测试方案搭建）同步开发，可能导致测试方案里有些地方测了旧代码。请对已经合并后的代码（push到github上的版本）里的测试方案审查一遍(测试方案可能存在表述不一致的情况)
13. 意图模糊kanban能不能识别项目路径
14. /issue后触发的评估分析阶段到issue发布到github阶段的前端优化：https://chatgpt.com/c/6abc6e85-f6bc-83ea-8fc5-a5d522e991d3 文件：/Test ZaoFu_Project/zaofu-issue-workspace-v3.html
15. github 仓库账号密码：rzwu-uisee/zaofu-self-issue rzwu-uisee WRZ1489903719wrz