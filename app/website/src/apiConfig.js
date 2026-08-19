/**
 * 开发环境（npm start）统一走 webpack dev server 同源代理，避免：
 * - https://localhost:3000 → http://localhost:7860 混合内容被拦截
 * - 不必要的跨域预检
 *
 * 生产构建仍使用绝对地址（或由环境变量覆盖）。
 */
const defaults = {
  UPLOAD_API_URL: 'http://localhost:5000/api',
  CONVERT_API_URL: 'http://localhost:8002',
  ORCHESTRATOR_API_URL: 'http://localhost:7860',
  REFLECTION_API_URL: 'http://localhost:8009',
  CITATION_AGENT_API_URL: 'http://localhost:8005',
  EXPERIMENT_AGENT_API_URL: 'http://localhost:8006',
  FORMAT_AGENT_API_URL: 'http://localhost:8007',
  LOGIC_AGENT_API_URL: 'http://localhost:8008',
};

function buildDevProxyUrls(origin) {
  return {
    UPLOAD_API_URL: `${origin}/proxy/pdf-api/api`,
    CONVERT_API_URL: `${origin}/proxy/pdftomd`,
    ORCHESTRATOR_API_URL: `${origin}/proxy/orchestrator`,
    REFLECTION_API_URL: `${origin}/proxy/reflection`,
    CITATION_AGENT_API_URL: `${origin}/proxy/agent-citation`,
    EXPERIMENT_AGENT_API_URL: `${origin}/proxy/agent-experiment`,
    FORMAT_AGENT_API_URL: `${origin}/proxy/agent-format`,
    LOGIC_AGENT_API_URL: `${origin}/proxy/agent-logic`,
  };
}

export function getApiUrls() {
  if (process.env.NODE_ENV === 'production') {
    return {
      ...defaults,
      ...(process.env.REACT_APP_UPLOAD_API_URL && {
        UPLOAD_API_URL: process.env.REACT_APP_UPLOAD_API_URL,
      }),
      ...(process.env.REACT_APP_ORCHESTRATOR_URL && {
        ORCHESTRATOR_API_URL: process.env.REACT_APP_ORCHESTRATOR_URL,
      }),
      ...(process.env.REACT_APP_REFLECTION_API_URL && {
        REFLECTION_API_URL: process.env.REACT_APP_REFLECTION_API_URL,
      }),
    };
  }

  if (typeof window !== 'undefined' && window.location?.origin) {
    return buildDevProxyUrls(window.location.origin);
  }

  return defaults;
}
