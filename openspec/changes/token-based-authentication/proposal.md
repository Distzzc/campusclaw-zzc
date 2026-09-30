## Why

当前登录依赖 Flask 签名 Cookie Session，浏览器页面和 API 均隐式携带 Cookie。为统一 API 与浏览器端的身份凭证，本变更将登录认证切换为显式 Bearer Token，同时保留现有账号密码校验、角色权限和班级隔离行为。

## What Changes

- **BREAKING**：登录成功后签发不透明访问令牌；受保护 API 仅接受 `Authorization: Bearer <token>`，不再接受 Cookie Session 作为认证凭证。
- 登录前端通过登录 API 获取令牌，在当前浏览器标签页的 `sessionStorage` 中保存，并为受保护 API 请求附加 Bearer 头；页面入口调整为由前端根据认证状态导航。
- 访问令牌设置有限有效期（初步按 1 小时规划），登出时撤销令牌；令牌缺失、无效、过期或已撤销均按未认证处理。
- 继续执行现有服务端角色授权和班级隔离，不把令牌中的客户端数据或 UI 控件作为权限依据。
- 新增登录、Bearer 认证、过期、撤销、旧 Cookie 不再生效等测试。

## Capabilities

### New Capabilities
- `token-based-authentication`: 定义令牌签发、客户端携带、有效期、撤销及受保护页面/API 的认证行为。

### Modified Capabilities

无。当前 `openspec/specs/` 中没有已建立的主规格；此前变更目录里的认证描述仅作为项目背景参考。

## Impact

- 影响 `app.py` 的登录、登出、请求身份加载和页面/API 认证保护；影响 `static/js/app.js` 与登录/受保护页面模板的凭证处理。
- 影响 `tests/test_app.py` 中依赖 Cookie Session 的登录和受保护资源测试。
- 令牌类型按推荐的最小安全方案规划为随机不透明 Bearer Token：服务端只保存令牌摘要，支持有效期校验和即时撤销；不引入 JWT 依赖。
- 浏览器令牌暂按 `sessionStorage` 保存，关闭标签页后清除；令牌过期后重新登录，不在本变更内引入刷新令牌。