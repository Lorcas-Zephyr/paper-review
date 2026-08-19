/**
 * 开发服务器代理：避免 https://localhost:3000 访问 http://localhost:7860 等被浏览器拦截（混合内容）。
 * 使用方式：前端在开发环境下将 API 基址设为 window.location.origin + /proxy/...
 */
const { createProxyMiddleware } = require('http-proxy-middleware');

module.exports = function setupProxy(app) {
  const strip = (prefix) => ({
    [`^${prefix}`]: '',
  });

  app.use(
    '/proxy/orchestrator',
    createProxyMiddleware({
      target: 'http://127.0.0.1:7860',
      changeOrigin: true,
      pathRewrite: strip('/proxy/orchestrator'),
      // 首次请求可能加载 BERT 等，避免 dev server 返回 504
      timeout: 600000,
      proxyTimeout: 600000,
    })
  );

  app.use(
    '/proxy/pdf-api',
    createProxyMiddleware({
      target: 'http://127.0.0.1:5000',
      changeOrigin: true,
      pathRewrite: strip('/proxy/pdf-api'),
    })
  );

  app.use(
    '/proxy/pdftomd',
    createProxyMiddleware({
      target: 'http://127.0.0.1:8002',
      changeOrigin: true,
      pathRewrite: strip('/proxy/pdftomd'),
    })
  );

  app.use(
    '/proxy/reflection',
    createProxyMiddleware({
      target: 'http://127.0.0.1:8009',
      changeOrigin: true,
      pathRewrite: strip('/proxy/reflection'),
    })
  );

  const agents = [
    ['/proxy/agent-citation', 8005],
    ['/proxy/agent-experiment', 8006],
    ['/proxy/agent-format', 8007],
    ['/proxy/agent-logic', 8008],
  ];
  agents.forEach(([prefix, port]) => {
    app.use(
      prefix,
      createProxyMiddleware({
        target: `http://127.0.0.1:${port}`,
        changeOrigin: true,
        pathRewrite: strip(prefix),
      })
    );
  });
};
