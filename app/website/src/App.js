import React, { useState, useRef, useEffect } from 'react';
import { Upload, Button, Card, Progress, Row, Col, Spin, Alert, message, Modal, Steps, Tag, Tooltip, List, Typography, Switch } from 'antd';
import {
  UploadOutlined, FilePdfOutlined, PlayCircleOutlined, ReloadOutlined,
  CloudUploadOutlined, CheckCircleOutlined,
  ExperimentOutlined, FormatPainterOutlined,
  BookOutlined, ApartmentOutlined, EyeOutlined,
  FileTextOutlined, LineChartOutlined, DownloadOutlined,
  QuestionCircleOutlined, SendOutlined, ThunderboltOutlined
} from '@ant-design/icons';
import axios from 'axios';
import './App.css';
import { getApiUrls } from './apiConfig';

// 解构出 Dragger
const { Dragger } = Upload;
const { Step } = Steps;
const { Paragraph } = Typography;

// API 基址：开发环境走 src/setupProxy.js 同源代理，避免 https 页面请求 http 后端被拦截
const {
  UPLOAD_API_URL,
  CONVERT_API_URL,
  ORCHESTRATOR_API_URL,
  REFLECTION_API_URL,
  CITATION_AGENT_API_URL,
  EXPERIMENT_AGENT_API_URL,
  FORMAT_AGENT_API_URL,
  LOGIC_AGENT_API_URL,
} = getApiUrls();

/** 构建产物位于 public/md_test/，开发时由 dev server 提供 */
const TEST_MD_URL = '/md_test/sample.md';

const App = () => {
  const [paperContent, setPaperContent] = useState('');
  const [reviewResults, setReviewResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [converting, setConverting] = useState(false);
  const [uploadedFile, setUploadedFile] = useState(null);
  const [fileSize, setFileSize] = useState(0);
  const [reviewProgress, setReviewProgress] = useState(0);
  const [apiStatus, setApiStatus] = useState({
    upload: { status: 'unknown', message: '' },
    convert: { status: 'unknown', message: '' },
    orchestrator: { status: 'unknown', message: '' },
    reflection: { status: 'unknown', message: '' },
    citation: { status: 'unknown', message: '' },
    experiment: { status: 'unknown', message: '' },
    format: { status: 'unknown', message: '' },
    logic: { status: 'unknown', message: '' }
  });
  const [auditTaskId, setAuditTaskId] = useState(null);
  const [taskStatus, setTaskStatus] = useState(null);
  const [auditStatus, setAuditStatus] = useState('idle');
  const [auditSteps, setAuditSteps] = useState([]);
  const [showAuditDetails, setShowAuditDetails] = useState(false);
  const [auditProgress, setAuditProgress] = useState(0);
  const [selectedFile, setSelectedFile] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [isTestMode, setIsTestMode] = useState(false);
  const [testLoadLoading, setTestLoadLoading] = useState(false);
  const [reportDownloadLoading, setReportDownloadLoading] = useState(false);
  /** 是否在反思阶段调用 DeepSeek 生成导师评语（关闭可略省耗时与 token） */
  const [enableMentorDialogue, setEnableMentorDialogue] = useState(true);
  /** 是否启用各审计组细则规则；关闭时为基本审阅（格式：布局+限块 LLM；逻辑：命题图速览；实验：仅大模型；文献：引用速览） */
  const [enableAuditRules, setEnableAuditRules] = useState(true);

  const pollIntervalRef = useRef(null);
  const fileInputRef = useRef(null);

  // 审计组配置
  const agentConfigs = {
    logic: {
      icon: <ApartmentOutlined style={{ color: '#1890ff' }} />,
      name: '逻辑审计组',
      description: '检查逻辑一致性、矛盾点、论证链条',
      groupId: 3,
      weight: 1.2
    },
    experiment: {
      icon: <ExperimentOutlined style={{ color: '#13c2c2' }} />,
      name: '实验数据审计组',
      description: '实验设计、数据显著性、结果可复现性',
      groupId: 5,
      weight: 1.1
    },
    format: {
      icon: <FormatPainterOutlined style={{ color: '#fa8c16' }} />,
      name: '格式审计组',
      description: '论文格式、图表编号、参考文献格式',
      groupId: 2,
      weight: 0.8
    },
    citation: {
      icon: <BookOutlined style={{ color: '#52c41a' }} />,
      name: '文献审计组',
      description: '参考文献验证、相关性、时效性',
      groupId: 6,
      weight: 1.0
    }
  };

  // 审计级别颜色映射
  const levelColors = {
    'Critical': '#ff4d4f', // 红色
    'Major': '#faad14', // 橙黄色
    'Warning': '#faad14',
    'Minor': '#1890ff', // 蓝色
    'Info': '#1890ff',
    'Pass': '#52c41a',
    'Failed': '#ff4d4f',
    'HIGH': '#722ed1',
    'Unknown': '#d9d9d9'
  };

  /** 与 orchestrator.agent_endpoints 顺序一致，保证矩阵始终展示 4 个审计组（含失败项） */
  const agentOrder = [
    { category: 'logic', agentName: 'logic_agent', groupId: 3 },
    { category: 'experiment', agentName: 'experiment_agent', groupId: 5 },
    { category: 'format', agentName: 'format_agent', groupId: 2 },
    { category: 'citation', agentName: 'citation_agent', groupId: 6 }
  ];

  // 检查API健康状态
  const checkApiHealth = async () => {
    setApiStatus({
      upload: { status: 'checking', message: '检查中...' },
      convert: { status: 'checking', message: '检查中...' },
      orchestrator: { status: 'checking', message: '检查中...' },
      reflection: { status: 'checking', message: '检查中...' },
      citation: { status: 'checking', message: '检查中...' },
      experiment: { status: 'checking', message: '检查中...' },
      format: { status: 'checking', message: '检查中...' },
      logic: { status: 'checking', message: '检查中...' }
    });

    const apis = [
      { key: 'upload', url: `${UPLOAD_API_URL}/health` },
      { key: 'convert', url: `${CONVERT_API_URL}/health` },
      { key: 'orchestrator', url: `${ORCHESTRATOR_API_URL}/health` },
      { key: 'reflection', url: `${REFLECTION_API_URL}/health` },
      { key: 'citation', url: `${CITATION_AGENT_API_URL}/health` },
      { key: 'experiment', url: `${EXPERIMENT_AGENT_API_URL}/health` },
      { key: 'format', url: `${FORMAT_AGENT_API_URL}/health` },
      { key: 'logic', url: `${LOGIC_AGENT_API_URL}/health` }
    ];

    for (const api of apis) {
      try {
        const response = await axios.get(api.url, {
          timeout: 20000,
          headers: { Accept: 'application/json' }
        });

        if (response.status === 200) {
          setApiStatus(prev => ({
            ...prev,
            [api.key]: {
              status: 'healthy',
              message: `${api.key.toUpperCase()} API正常`,
              details: response.data
            }
          }));
        } else {
          throw new Error(`状态码: ${response.status}`);
        }
      } catch (error) {
        console.error(`API ${api.key} 检查失败:`, error);
        setApiStatus(prev => ({
          ...prev,
          [api.key]: {
            status: 'unhealthy',
            message: `${api.key.toUpperCase()} API连接失败`,
            error: error.message
          }
        }));
      }
    }
  };

  useEffect(() => {
    checkApiHealth();
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, []);

  // 上传PDF文件到上传API
  const uploadPDF = async (file) => {
    const formData = new FormData();
    formData.append('file', file);

    try {
      setLoading(true);

      const response = await axios.post(`${UPLOAD_API_URL}/upload`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
        onUploadProgress: (progressEvent) => {
          if (progressEvent.total) {
            const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
            setReviewProgress(percent);
          }
        },
        timeout: 30000000,
      });

      if (response.data.success) {
        message.success('PDF上传成功！');
        return response.data;
      } else {
        throw new Error(response.data.error || '上传失败');
      }
    } catch (error) {
      console.error('上传失败:', error);

      let errorMessage = '上传失败';
      if (error.response) {
        errorMessage = error.response.data?.error || error.response.data?.detail || errorMessage;
      } else if (error.message) {
        errorMessage = error.message;
      }

      message.error(errorMessage);
      return null;
    } finally {
      setLoading(false);
      setReviewProgress(0);
    }
  };

  // 调用转换API转换PDF
  const convertPDF = async (uploadResult) => {
    if (!uploadResult) {
      message.warning('请先上传PDF文件');
      return null;
    }

    try {
      setConverting(true);
      setReviewProgress(0);

      console.log('📤 调用8002端口API进行PDF解析');

      // 调用8002端口的增强API
      const response = await axios.post(`${CONVERT_API_URL}/convert/from-path`, {
        file_path: uploadResult.file_path,
        config: {
          // lang_list: ["zh"],
          backend: "pipeline",
          formula_enable: false,
          table_enable: false
        }
      }, {
        timeout: 30000000,
        onUploadProgress: (progressEvent) => {
          if (progressEvent.total) {
            const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
            setReviewProgress(Math.min(90, 20 + percent * 0.7));
          }
        }
      });

      console.log('✅ 8002端口API响应:', response.data);

      if (response.data.success) {
        setReviewProgress(100);
        message.success('PDF解析成功！');

        // 从正确的位置获取markdown内容
        const markdownContent = response.data.markdown ||
                               response.data.markdown_content ||
                               (response.data.files?.markdown?.content) ||
                               '';

        return {
          success: true,
          markdown: markdownContent,
          pageCount: response.data.markdown_length || 0,
          charCount: markdownContent.length,
          download_urls: response.data.download_urls,
          content_list: response.data.content_list || response.data.files?.content_list
        };
      } else {
        throw new Error(response.data.error || '解析失败');
      }
    } catch (error) {
      console.error('❌ PDF解析失败:', error);

      let errorMessage = 'PDF解析失败';
      if (error.response) {
        errorMessage = error.response.data?.error || error.response.data?.detail || errorMessage;
        console.error('错误详情:', error.response.data);
      } else if (error.message) {
        errorMessage = error.message;
      }

      message.error(`解析失败: ${errorMessage}`);
      return null;
    } finally {
      setConverting(false);
    }
  };

  // 完整流程：上传 + 转换
  const processPDFFile = async (file) => {
    setSelectedFile(file);
    setIsTestMode(false);

    // 1. 上传PDF到上传API
    const uploadResult = await uploadPDF(file);

    if (!uploadResult) {
      return null;
    }

    // 更新文件信息状态
    setUploadedFile(uploadResult.file_name);
    setFileSize(uploadResult.file_size);

    // 2. 调用PDF解析API
    const parseResult = await convertPDF(uploadResult);

    if (parseResult && parseResult.success) {
      // 设置Markdown内容
      setPaperContent(parseResult.markdown);

      // 返回完整结果
      return {
        upload: uploadResult,
        parse: parseResult,
        markdown: parseResult.markdown,
        pageCount: parseResult.page_count || 0,
        charCount: parseResult.total_chars || 0,
        content_list: parseResult.content_list
      };
    }

    return null;
  };

  /** 跳过上传与 PDF→MD，加载 public/md_test 下的假 Markdown，用于调试后续评审链路 */
  const loadTestMarkdown = async () => {
    setTestLoadLoading(true);
    try {
      const res = await fetch(TEST_MD_URL, { cache: 'no-store' });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const text = await res.text();
      setPaperContent(text);
      setUploadedFile('md_test_sample.md');
      setFileSize(new Blob([text]).size);
      setSelectedFile(null);
      setIsTestMode(true);
      setReviewResults(null);
      message.success('已加载测试 Markdown，可直接点击「开始多智能体协同评审」');
    } catch (e) {
      console.error(e);
      message.error(`加载 ${TEST_MD_URL} 失败，请确认 public/md_test/sample.md 存在`);
    } finally {
      setTestLoadLoading(false);
    }
  };

  const paperTitleFromUploadName = () => {
    const name = uploadedFile || 'paper';
    return name.replace(/\.(pdf|md)$/i, '').trim() || '未命名论文';
  };

  // 手动调用智能评阅（调度器）
  const startReview = async () => {
    if (!uploadedFile || !paperContent) {
      message.warning('请先上传并转换 PDF，或使用「加载测试 Markdown」');
      return;
    }

    setLoading(true);
    setAuditStatus('pending');
    setReviewProgress(0);
    setAuditProgress(0);

    // 初始化审计步骤
    const steps = [
      { title: '提交评审任务', status: 'wait', description: '等待提交' },
      { title: '逻辑审计组', status: 'wait', description: '等待处理' },
      { title: '实验数据审计组', status: 'wait', description: '等待处理' },
      { title: '格式审计组', status: 'wait', description: '等待处理' },
      { title: '文献审计组', status: 'wait', description: '等待处理' },
      { title: '结果聚合', status: 'wait', description: '等待处理' }
    ];
    setAuditSteps(steps);

    try {
      // 1. 提交到调度器
      setAuditStatus('running');
      updateStepStatus(0, 'process', '正在提交到调度器...');

      const response = await axios.post(
        `${ORCHESTRATOR_API_URL}/api/v1/audit`,
        {
          title: paperTitleFromUploadName(),
          content: paperContent,
          config: {
            ...(isTestMode ? { debug_test_md: true } : {}),
            enable_mentor_dialogue: enableMentorDialogue,
            enable_rules: enableAuditRules,
          },
        },
        {
          timeout: 30000000,
          headers: {
            'Content-Type': 'application/json',
          },
        }
      );

      if (response.data && response.data.request_id) {
        const taskId = response.data.request_id;
        setAuditTaskId(taskId);
        setTaskStatus(response.data);
        updateStepStatus(0, 'finish', '任务已提交');

        message.success('评审任务已提交，正在处理中...');

        // 2. 开始轮询任务状态
        pollTaskStatus(taskId);
      } else {
        throw new Error('调度器返回格式错误');
      }

    } catch (error) {
      console.error('提交评审失败:', error);
      setAuditStatus('failed');
      updateStepStatus(0, 'error', '提交失败');

      let errorMessage = '提交评审失败';
      if (error.response) {
        console.error('错误详情:', error.response.data);
        errorMessage = error.response.data?.detail || error.response.data?.message || errorMessage;
      } else if (error.message) {
        errorMessage = error.message;
      }

      message.error(errorMessage);
      setLoading(false);
    }
  };

  // 更新审计步骤状态
  const updateStepStatus = (index, status, description) => {
    setAuditSteps(prev => {
      const newSteps = [...prev];
      newSteps[index] = { ...newSteps[index], status, description };
      return newSteps;
    });
  };

  // 轮询任务状态
  const pollTaskStatus = (taskId) => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
    }

    pollIntervalRef.current = setInterval(async () => {
      try {
        const response = await axios.get(`${ORCHESTRATOR_API_URL}/api/v1/task/${taskId}`, {
          timeout: 100000000,
        });

        const status = response.data;
        setTaskStatus(status);

        // 更新整体进度
        if (status.progress) {
          const totalTasks = Object.keys(status.progress).length;
          const completedTasks = Object.values(status.progress).filter(s => s === 'SUCCESS').length;
          const progress = Math.round((completedTasks / totalTasks) * 100) || 0;
          setAuditProgress(progress);
          setReviewProgress(progress);

          // 更新各个Agent的状态
          updateAgentProgress(status.progress);
        }

        // 检查任务完成状态
        if (status.overall_status === 'SUCCESS') {
          clearInterval(pollIntervalRef.current);
          setAuditStatus('success');
          setLoading(false);

          // 处理最终结果
          if (status.aggregated_report) {
            processAuditResults(status.aggregated_report);
            updateStepStatus(5, 'finish', '结果聚合完成');
            message.success('智能评审完成！');
          } else {
            // 如果没有聚合报告，使用状态数据
            processTaskStatus(status);
          }
        } else if (status.overall_status === 'FAILED') {
          clearInterval(pollIntervalRef.current);
          setAuditStatus('failed');
          setLoading(false);
          updateStepStatus(5, 'error', '处理失败');
          message.error('评审任务失败');
        }

      } catch (error) {
        console.error('轮询任务状态失败:', error);
        // 继续轮询，不中断
      }
    }, 2000);
  };

  // 更新Agent进度状态
  const updateAgentProgress = (progressDict) => {
    const agentMapping = {
      'logic_agent': 1,
      'experiment_agent': 2,
      'format_agent': 3,
      'citation_agent': 4
    };

    Object.entries(progressDict).forEach(([key, status]) => {
      const [agentName] = key.split('_chunk_');
      if (agentMapping[agentName] !== undefined) {
        const stepIndex = agentMapping[agentName];

        if (status === 'SUCCESS') {
          updateStepStatus(stepIndex, 'finish', '处理完成');
        } else if (status === 'RUNNING') {
          updateStepStatus(stepIndex, 'process', '正在处理');
        } else if (status === 'FAILED') {
          updateStepStatus(stepIndex, 'error', '处理失败');
        }
      }
    });
  };

  /** 去掉 evidence/正文中重复的「级别: Critical」等与列表 Tag 重复的片段 */
  const stripEmbeddedIssueLevel = (text) => {
    if (!text || typeof text !== 'string') return '';
    return text
      .replace(/\s*级别[:：]\s*(Critical|Major|Minor|Warning|Pass|Info)\s*/gi, ' ')
      .replace(/\s*\(?(严重度|等级|级别)[:：]\s*(Critical|Major|Minor)\)?\s*/gi, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  };

  /**
   * 与 audit_reflection severity_calibration 对齐：纯缩写/英文全称书写格式类不应展示为 Critical。
   * 用于缓存或未重跑反思时的列表展示兜底。
   */
  const isBenignCriticalIssueText = (text) => {
    const s = String(text || '');
    if (!s.trim()) return false;
    if (/(伪造|篡改|造假|学术不端|数据造假|结论不成立|逻辑谬误)/.test(s)) return false;
    if (s.includes('术语缩写定义格式不一致')) return true;
    if (s.includes('定义格式不一致') && s.includes('英文全称')) return true;
    if (
      s.includes('英文全称') &&
      (s.includes('缺少空格') || s.includes('应为小写') || s.includes('应为大写') || s.includes('后缺少'))
    ) {
      return true;
    }
    // 正文/表数值一致 + 误差棒类呈现问题，不应标 Critical（与 severity_calibration 对齐）
    if (
      s.includes('数值一致') &&
      !s.includes('数值不一致') &&
      (s.includes('表') || s.includes('图表') || s.includes('误差棒') || s.includes('不确定性'))
    ) {
      return true;
    }
    if (s.includes('不应标为Critical') || s.includes('裁决为内部意见整合')) return true;
    return false;
  };

  // 将反思评估 issues 转为表格/列表行
  const buildReflectionIssueRows = (reflection) => {
    if (!reflection) return [];
    const rows = [];
    const push = (arr, tag) => {
      (arr || []).forEach((issue) => {
        const o = typeof issue === 'object' && issue !== null ? issue : { description: String(issue) };
        let cleanTitle = o.description || '';
        cleanTitle = cleanTitle.replace(/^(\[.*?\]\s*)+/, '').trim();
        cleanTitle = stripEmbeddedIssueLevel(cleanTitle);
        const rawLoc = o.evidence || (Array.isArray(o.agents) ? o.agents.join('、') : '') || '';
        const location = stripEmbeddedIssueLevel(rawLoc);
        let level = tag;
        if (tag === 'Critical' && isBenignCriticalIssueText(`${cleanTitle} ${location}`)) {
          level = 'Major';
        }
        rows.push({
          title: cleanTitle,
          location,
          description: o.description || '',
          suggestion: (o.suggestion || '').trim(),
          level
        });
      });
    };
    push(reflection.critical_issues, 'Critical');
    push(reflection.major_issues, 'Major');
    push(reflection.minor_issues, 'Minor');

    const levelRank = { Critical: 3, Major: 2, Minor: 1, Warning: 2, Info: 0, Pass: 0 };
    const byTitle = new Map();
    for (const r of rows) {
      const k = (r.title || '').replace(/\s+/g, ' ').trim().toLowerCase();
      if (!k) continue;
      const prev = byTitle.get(k);
      const rnk = levelRank[r.level] ?? 0;
      const pnk = prev ? (levelRank[prev.level] ?? 0) : -1;
      if (!prev || rnk > pnk) {
        byTitle.set(k, { ...r });
      }
    }
    const out = Array.from(byTitle.values());
    out.sort(
      (a, b) =>
        (levelRank[b.level] ?? 0) - (levelRank[a.level] ?? 0) ||
        String(a.title || '').localeCompare(String(b.title || ''), 'zh-CN')
    );
    return out;
  };

  // 处理审计结果
  const processAuditResults = (aggregatedReport) => {
    const score = aggregatedReport.overall_score ?? 0;
    const reflection = aggregatedReport.reflection || null;
    const verdictText = aggregatedReport.verdict || (reflection && reflection.verdict) || '';

    const paperId =
      aggregatedReport.paper_id ||
      (reflection && reflection.paper_id) ||
      null;

    const formattedResults = {
      overall_score: score,
      grade: getGrade(score),
      conclusion: verdictText || getConclusion(score),
      paper_id: paperId,
      review_items: buildReviewItemsFromDetails(aggregatedReport),
      logic_review_details: getReviewDetails(aggregatedReport, 'logic'),
      data_review_details: getReviewDetails(aggregatedReport, 'experiment'),
      format_review_details: getReviewDetails(aggregatedReport, 'format'),
      citation_review_details: getReviewDetails(aggregatedReport, 'citation'),
      reflection_review_details: buildReflectionIssueRows(reflection),
      reflection,
      needs_human_review: aggregatedReport.needs_human_review,
      human_review_reason: aggregatedReport.human_review_reason,
      verdict: verdictText,
      aggregated_report: aggregatedReport,
      audit_details: aggregatedReport.details_by_chunk || {},
      group_results: buildFullGroupResults(aggregatedReport)
    };

    setReviewResults(formattedResults);
  };

  /** 从反思服务下载本轮 Markdown 评估报告 */
  const downloadReflectionReport = async () => {
    const pid =
      reviewResults?.paper_id ||
      reviewResults?.aggregated_report?.paper_id ||
      reviewResults?.reflection?.paper_id;
    if (!pid) {
      message.warning('暂无论文 ID，无法下载报告');
      return;
    }
    setReportDownloadLoading(true);
    try {
      const base = String(REFLECTION_API_URL || '').replace(/\/$/, '');
      const res = await fetch(`${base}/api/report/${encodeURIComponent(pid)}`);
      if (!res.ok) {
        throw new Error(await res.text().catch(() => `HTTP ${res.status}`));
      }
      const blob = await res.blob();
      const dispo = res.headers.get('Content-Disposition') || '';
      let fname = `review_report_${pid}.md`;
      const m = /filename\*?=(?:UTF-8'')?["']?([^";\n]+)/i.exec(dispo);
      if (m && m[1]) {
        try {
          fname = decodeURIComponent(m[1].replace(/['"]/g, '').trim());
        } catch {
          fname = m[1].replace(/['"]/g, '').trim();
        }
      }
      const href = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = href;
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(href);
      message.success('报告已开始下载');
    } catch {
      message.error('下载失败：请确认反思服务已启动且本轮已生成报告');
    } finally {
      setReportDownloadLoading(false);
    }
  };

  // 处理任务状态（无聚合报告时不使用模拟数据）
  const processTaskStatus = (taskStatus) => {
    message.warning(taskStatus?.message || '未获取到聚合评审报告，请稍后重试或查看调度器日志');
    setReviewResults(null);
  };

  // 综合质量等级（与反思评估 thesis_grade_verdict 四档一致：≥90 / 80–89 / 70–79 / <70）
  const getGrade = (score) => {
    if (score >= 90) return '优秀 (A)';
    if (score >= 80) return '良好 (B)';
    if (score >= 70) return '一般 (C)';
    return '较差/不通过 (F)';
  };

  /** 与 orchestrator.audit_level_from_score 一致：按组综合分映射 Critical / Warning / Pass */
  const auditLevelFromScore = (score) => {
    const s = Number(score);
    if (!Number.isFinite(s)) return 'Pass';
    if (s < 60) return 'Critical';
    if (s < 80) return 'Warning';
    return 'Pass';
  };

  // 无 reflection.verdict 时的回退文案（与指标文档第 12 节四档描述一致）
  const getConclusion = (score) => {
    if (score >= 90) {
      return '选题属前沿；文献综述全面且具有批判性；具有明显的新方法或新见解；实验可靠且对比充分；文字表达严谨、无低级错误。';
    }
    if (score >= 80) {
      return '选题难度适中；基本了解国内外动态；有一定新意；实验方案合理；仅有极少量文字瑕疵。';
    }
    if (score >= 70) {
      return '基本达到硕士水平，但创新性不强，实验对比不够充分，文字排版存在较多不规范之处。';
    }
    return '选题难度不够；拼凑痕迹明显；关键技术无横向对比；文字错误率极高。';
  };

  /** 从调度器 details_by_agent 生成矩阵行（固定 4 组，失败时仍展示并标 Failed） */
  const buildReviewItemsFromDetails = (aggregatedReport) => {
    const byAgent = aggregatedReport?.details_by_agent || {};
    return agentOrder.map(({ category, agentName }) => {
      const cfg = agentConfigs[category];
      const d = byAgent[agentName];
      if (!d) {
        return {
          title: cfg.name,
          category,
          score: 0,
          status: 'Unknown',
          description: cfg.description
        };
      }
      const ok = d.status === 'SUCCESS';
      const status = ok ? auditLevelFromScore(d.score) : 'Failed';

      const fallbackHint = d.score_fallback
        ? '（调度器未解析到分数，已用随机分占位，请以 Agent 原始响应为准）'
        : '';
      const failText = [d.error, d.response_text].filter(Boolean).join(' — ') || '该审计组调用失败';

      return {
        title: cfg.name,
        category,
        score: ok ? Math.round(Number(d.score) || 0) : 0,
        status,
        description: ok ? `${cfg.description}${fallbackHint ? ` ${fallbackHint}` : ''}` : failText
      };
    });
  };

  /** 综合评分区「审计组评分详情」与矩阵一致：始终 4 条 */
  const buildFullGroupResults = (aggregatedReport) => {
    const byAgent = aggregatedReport?.details_by_agent || {};
    return agentOrder.map(({ category, agentName, groupId }) => {
      const cfg = agentConfigs[category];
      const d = byAgent[agentName];
      const ok = d && d.status === 'SUCCESS';
      const displayLevel = ok ? auditLevelFromScore(d.score) : 'Failed';

      return {
        group_id: groupId,
        group_name: cfg.name,
        audit_results: [
          {
            level: displayLevel,
            score: ok ? Math.round(Number(d.score) || 0) : 0
          }
        ]
      };
    });
  };

  // 根据组名确定组类型
  const getGroupTypeFromName = (groupName) => {
    if (!groupName) return 'logic';

    const nameLower = groupName.toLowerCase();
    if (nameLower.includes('逻辑')) return 'logic';
    if (nameLower.includes('实验')) return 'experiment';
    if (nameLower.includes('格式')) return 'format';
    if (nameLower.includes('文献')) return 'citation';
    return 'logic';
  };

  // 获取详细评审信息（按 group_id / 中文组名匹配）
  const getReviewDetails = (aggregatedReport, agentType) => {
    const gid = (g) => {
      const v = g.group_id;
      const n = typeof v === 'string' ? parseInt(v, 10) : v;
      return Number.isFinite(n) ? n : null;
    };
    const matchers = {
      logic: (g) => gid(g) === 3 || (g.group_name && g.group_name.includes('逻辑')),
      experiment: (g) => gid(g) === 5 || (g.group_name && g.group_name.includes('实验')),
      format: (g) => gid(g) === 2 || (g.group_name && g.group_name.includes('格式')),
      citation: (g) => gid(g) === 6 || (g.group_name && g.group_name.includes('文献'))
    };
    const match = matchers[agentType];
    const group = match
      ? aggregatedReport.group_results?.find((g) => match(g))
      : null;

    if (!group || !group.audit_results) return [];

    return group.audit_results.map(item => ({
      title: item.point,
      location: `章节: ${item.location?.section || '未指定'}`,
      description: item.description,
      suggestion: item.suggestion,
      level: item.level,
      score: item.score
    }));
  };

  // 重置状态
  const resetState = () => {
    setPaperContent('');
    setReviewResults(null);
    setUploadedFile(null);
    setFileSize(0);
    setReviewProgress(0);
    setAuditProgress(0);
    setAuditTaskId(null);
    setTaskStatus(null);
    setAuditStatus('idle');
    setAuditSteps([]);
    setSelectedFile(null);
    setIsTestMode(false);

    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
    }

    message.info('已重置所有状态');
  };

  // 手动触发文件选择
  const triggerFileSelect = () => {
    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  };

  // 下载Markdown文件
  const downloadMarkdown = () => {
    if (!paperContent) {
      message.warning('没有Markdown内容可下载');
      return;
    }

    const blob = new Blob([paperContent], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const base = (uploadedFile || 'document').replace(/\.(pdf|md)$/i, '');
    a.download = `${base || 'document'}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    message.success('Markdown文件下载成功！');
  };

  // 文件上传配置
  const uploadProps = {
    name: 'file',
    multiple: false,
    accept: '.pdf',
    showUploadList: false,
    beforeUpload: (file) => {
      if (!file.type.includes('pdf') && !file.name.toLowerCase().endsWith('.pdf')) {
        message.error('只支持PDF格式的文件');
        return false;
      }

      const isLt200M = file.size / 1024 / 1024 < 200;
      if (!isLt200M) {
        message.error('文件大小不能超过200MB');
        return false;
      }

      processPDFFile(file);
      return false;
    },
    onDrop: (e) => {
      setIsDragging(false);
    },
    onDragEnter: () => {
      setIsDragging(true);
    },
    onDragLeave: () => {
      setIsDragging(false);
    }
  };

  // 渲染审计组状态面板
  const renderAgentStatusPanel = () => {
    if (!taskStatus || !taskStatus.progress) return null;

    return (
      <Card title="审计组执行状态" size="small" className="agent-status-card">
        <Row gutter={[16, 16]}>
          {Object.entries(agentConfigs).map(([key, config]) => {
            const agentTasks = Object.entries(taskStatus.progress || {})
              .filter(([taskKey]) => taskKey.startsWith(`${key}_agent`))
              .map(([taskKey, status]) => ({ taskKey, status }));

            const successCount = agentTasks.filter(task => task.status === 'SUCCESS').length;
            const totalCount = agentTasks.length;
            const progress = totalCount > 0 ? Math.round((successCount / totalCount) * 100) : 0;

            return (
              <div key={key} className="agent-card-item">
                <Card size="small" className="agent-card">
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      {config.icon}
                      <Tooltip title={config.description}>
                        <span>{config.name}</span>
                      </Tooltip>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <Tag color={
                        progress === 100 ? 'success' :
                        progress > 0 ? 'processing' : 'default'
                      }>
                        {progress === 100 ? '完成' : `${progress}%`}
                      </Tag>
                      <span style={{ fontSize: 12, color: '#999' }}>
                        {successCount}/{totalCount}
                      </span>
                    </div>
                  </div>
                  <Progress
                    percent={progress}
                    size="small"
                    strokeColor={progress === 100 ? '#52c41a' : '#1890ff'}
                    style={{ marginTop: 8 }}
                  />
                </Card>
              </div>
            );
          })}
        </Row>
      </Card>
    );
  };

  // 渲染API状态指示器
  const renderApiStatus = () => (
    <div className="api-status">
      {Object.entries(apiStatus).map(([key, status]) => (
        <div key={key} className={`api-status-item ${status.status}`}>
          {key.toUpperCase()}:
          {status.status === 'healthy' ? '✅' :
           status.status === 'checking' ? '⏳' : '❌'}
          {status.message}
        </div>
      ))}
    </div>
  );

  /**
   * 主流程进度（与实际上传 / 转换 / 提交 / 轮询状态对齐）
   * - null: 尚未开始
   * - 0..4: 当前进行中的步骤索引
   * - 'done': 已全部完成（含查看结果）
   * - 'failed_at_submit': 提交调度器失败
   * - 'failed_at_review': 评审过程失败
   */
  const getMainWorkflowPhase = () => {
    if (reviewResults && auditStatus === 'success') return 'done';
    if (auditStatus === 'success' && !reviewResults) return 4;
    if (auditStatus === 'failed') {
      if (!auditTaskId) return 'failed_at_submit';
      return 'failed_at_review';
    }
    if (auditStatus === 'running' || auditStatus === 'pending') return 3;
    if (paperContent && (uploadedFile || isTestMode)) return 2;
    if (converting) return 1;
    if (loading && selectedFile && !paperContent) return 0;
    return null;
  };

  const renderMainWorkflowSteps = () => {
    const phase = getMainWorkflowPhase();
    const phases = [
      { title: '上传PDF', description: '选择并上传文件', icon: <CloudUploadOutlined /> },
      { title: '转换 Markdown', description: '解析为正文', icon: <FileTextOutlined /> },
      { title: '提交评审', description: '提交调度器', icon: <SendOutlined /> },
      { title: '智能评审', description: '多智能体协同', icon: <ThunderboltOutlined /> },
      { title: '查看结果', description: '评分与反思报告', icon: <EyeOutlined /> },
    ];

    const items = phases.map((p, i) => {
      let status = 'wait';
      if (phase === 'done') {
        status = 'finish';
      } else if (phase === 'failed_at_submit') {
        if (i < 2) status = 'finish';
        else if (i === 2) status = 'error';
        else status = 'wait';
      } else if (phase === 'failed_at_review') {
        if (i < 3) status = 'finish';
        else if (i === 3) status = 'error';
        else status = 'wait';
      } else if (phase === null) {
        status = 'wait';
      } else if (typeof phase === 'number') {
        if (i < phase) status = 'finish';
        else if (i === phase) status = 'process';
        else status = 'wait';
      }
      return {
        title: p.title,
        description: p.description,
        icon: p.icon,
        status,
      };
    });

    return (
      <div className="workflow-steps-wrap">
        <div className="workflow-steps-inner">
          <div className="workflow-steps-label">评阅流程</div>
          <Steps size="small" responsive items={items} className="main-workflow-steps" />
        </div>
      </div>
    );
  };

  const reflectionDetailRows = reviewResults?.reflection_review_details ?? [];
  const hasMentorComments = Boolean(
    reviewResults?.reflection &&
      Array.isArray(reviewResults.reflection.mentor_dialogue?.conversation) &&
      reviewResults.reflection.mentor_dialogue.conversation.length > 0
  );
  const critIssues = reflectionDetailRows.filter((x) => x.level === 'Critical');
  const majorIssues = reflectionDetailRows.filter((x) => x.level === 'Major');
  const minorIssues = reflectionDetailRows.filter((x) => x.level === 'Minor');
  const allIssuesOrdered = [...critIssues, ...majorIssues, ...minorIssues];

  const renderReflectionListItem = (item) => (
    <List.Item>
      <div>
        <div style={{ fontWeight: 500 }}>
          <Tag color={levelColors[item.level] || '#d9d9d9'} style={{ marginRight: 8 }}>{item.level}</Tag>
          {item.title}
        </div>
        {item.location && <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 4 }}>{item.location}</div>}
        {item.suggestion && <div style={{ marginTop: 4 }}>建议：{item.suggestion}</div>}
      </div>
    </List.Item>
  );

  return (
    <div className="app-container">
      {/* 顶部导航栏 */}
      <header className="app-header">
        <div className="header-main">
          <h1>硕士论文质量智能评阅系统</h1>
          <div className="header-subtitle">
            基于多智能体协同评审架构
          </div>
        </div>

        {renderApiStatus()}

        <div className="header-actions">
          <Button
            icon={<ReloadOutlined />}
            onClick={checkApiHealth}
            size="small"
            type="text"
          >
            检查API
          </Button>
          <Button
            icon={<QuestionCircleOutlined />}
            onClick={() => setShowHelp(true)}
            size="small"
            type="text"
          >
            帮助
          </Button>
        </div>
      </header>

      {renderMainWorkflowSteps()}

      <div className="main-content">
        {/* 左侧栏：论文处理区域 */}
        <div className="left-panel">
          <Card
            size="small"
            className="debug-test-card"
            style={{ marginBottom: 16 }}
            title={
              <span>
                <ExperimentOutlined style={{ marginRight: 8, color: '#722ed1' }} />
                调试：跳过 PDF / 转 MD
              </span>
            }
          >
            <p style={{ marginBottom: 12, color: '#595959', fontSize: 13 }}>
              从 <code>public/md_test/sample.md</code> 读取假论文正文，不经过上传与解析服务，便于单独调试调度器、四 Agent 与反思评估。
            </p>
            <Button
              type="dashed"
              block
              icon={<ExperimentOutlined />}
              loading={testLoadLoading}
              onClick={loadTestMarkdown}
            >
              加载测试 Markdown（md_test）
            </Button>
            {isTestMode && (
              <div style={{ marginTop: 10 }}>
                <Tag color="purple">测试模式</Tag>
                <span style={{ marginLeft: 8, fontSize: 12, color: '#8c8c8c' }}>
                  可直接点下方「开始多智能体协同评审」
                </span>
              </div>
            )}
          </Card>

          <Card
            title={
              <div className="card-title">
                <FilePdfOutlined /> 论文上传与转换
              </div>
            }
            className="paper-card"
            extra={
              <div className="card-extra">
                {uploadedFile && (
                  <Button
                    size="small"
                    onClick={downloadMarkdown}
                    icon={<DownloadOutlined />}
                  >
                    下载Markdown
                  </Button>
                )}
              </div>
            }
          >
            {!uploadedFile ? (
              <div className="upload-section">
                <Dragger {...uploadProps} className={`upload-dragger ${isDragging ? 'dragging' : ''}`}>
                  <p className="ant-upload-drag-icon">
                    <CloudUploadOutlined />
                  </p>
                  <p className="ant-upload-text">点击或拖拽论文文件 (PDF)</p>
                  <p className="ant-upload-hint">支持单个文件上传，最大200MB</p>
                  <p className="ant-upload-hint">文件将自动转换为Markdown格式</p>
                </Dragger>

                <input
                  type="file"
                  ref={fileInputRef}
                  accept=".pdf"
                  style={{ display: 'none' }}
                  onChange={(e) => {
                    if (e.target.files && e.target.files[0]) {
                      processPDFFile(e.target.files[0]);
                    }
                  }}
                />

                <div style={{ textAlign: 'center', marginTop: 20 }}>
                  <Button
                    type="dashed"
                    icon={<UploadOutlined />}
                    onClick={triggerFileSelect}
                    size="large"
                  >
                    选择PDF文件
                  </Button>
                </div>

                {selectedFile && (
                  <div style={{ marginTop: 20, padding: 10, background: '#f6ffed', borderRadius: 6 }}>
                    <FilePdfOutlined style={{ marginRight: 8 }} />
                    {selectedFile.name} ({(selectedFile.size / 1024 / 1024).toFixed(2)} MB)
                    {loading && <Spin size="small" style={{ marginLeft: 10 }} />}
                  </div>
                )}
              </div>
            ) : (
              <div className="paper-content">
                <div className="file-header">
                  <div className="file-info">
                    <FilePdfOutlined />
                    <span className="filename">{uploadedFile}</span>
                    <span className="filesize">({(fileSize / 1024 / 1024).toFixed(2)}MB)</span>
                  </div>
                  <div className="file-actions">
                    <Button
                      size="small"
                      onClick={downloadMarkdown}
                      icon={<FileTextOutlined />}
                    >
                      下载Markdown
                    </Button>
                    <Button
                      size="small"
                      onClick={resetState}
                      danger
                      icon={<ReloadOutlined />}
                    >
                      重置
                    </Button>
                  </div>
                </div>

                <div className="markdown-container">
                  {(loading || converting) ? (
                    <div className="converting-overlay">
                      <Spin
                        size="large"
                        tip={loading ? "上传中..." : converting ? "转换中..." : "处理中..."}
                      />
                      <Progress
                        percent={reviewProgress}
                        status="active"
                        strokeColor="#1890ff"
                        style={{ width: '60%', marginTop: 20 }}
                      />
                    </div>
                  ) : paperContent ? (
                    <div className="markdown-content">
                      <h3>Markdown预览（前1000字符）</h3>
                      <div className="markdown-preview">
                        {paperContent.substring(0, 1000)}
                        {paperContent.length > 1000 && '...'}
                      </div>
                      <div style={{ marginTop: 10, fontSize: 12, color: '#666' }}>
                        总字符数: {paperContent.length} | 总行数: {paperContent.split('\n').length}
                      </div>
                    </div>
                  ) : (
                    <Alert
                      message="等待转换结果"
                      description="PDF文件已上传，正在等待转换处理..."
                      type="info"
                      showIcon
                    />
                  )}
                </div>
              </div>
            )}
          </Card>

          {/* 控制按钮区 */}
          {uploadedFile && paperContent && (
            <div className="control-buttons">
              <div
                style={{
                  marginBottom: 12,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  flexWrap: 'wrap',
                  gap: 8,
                }}
              >
                <Tooltip title="开启时反思服务会调用大模型生成「导师评语」；关闭可缩短总耗时并减少一次 API 调用">
                  <span style={{ color: '#595959', fontSize: 14 }}>
                    <span style={{ marginRight: 8 }}>生成导师评语</span>
                    <Switch
                      checked={enableMentorDialogue}
                      onChange={setEnableMentorDialogue}
                      disabled={loading}
                      checkedChildren="开"
                      unCheckedChildren="关"
                    />
                  </span>
                </Tooltip>
                <Tooltip title="关闭时四组均不跑数据库/YAML 等细则：格式保留布局与限块 LLM；逻辑仅输出命题图速览；实验仅大模型；文献仅引用形态速览">
                  <span style={{ color: '#595959', fontSize: 14 }}>
                    <span style={{ marginRight: 8 }}>审计细则</span>
                    <Switch
                      checked={enableAuditRules}
                      onChange={setEnableAuditRules}
                      disabled={loading}
                      checkedChildren="开"
                      unCheckedChildren="关"
                    />
                  </span>
                </Tooltip>
              </div>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                onClick={startReview}
                loading={loading && auditStatus !== 'idle'}
                disabled={loading || !paperContent}
                className="review-button"
                size="large"
                block
              >
                {loading ? `评审中... ${reviewProgress}%` : '开始多智能体协同评审'}
              </Button>

              {/* 审计进度步骤 */}
              {auditStatus !== 'idle' && auditSteps.length > 0 && (
                <Card size="small" className="audit-steps-card" style={{ marginTop: 16 }}>
                  <Steps current={auditSteps.findIndex(s => s.status === 'process')} size="small">
                    {auditSteps.map((step, index) => (
                      <Step
                        key={index}
                        title={step.title}
                        description={step.description}
                        status={step.status}
                      />
                    ))}
                  </Steps>
                  <Progress
                    percent={auditProgress}
                    status={auditStatus === 'success' ? 'success' :
                           auditStatus === 'failed' ? 'exception' : 'active'}
                    style={{ marginTop: 16 }}
                  />
                </Card>
              )}

              <div className="secondary-buttons" style={{ marginTop: 16 }}>
                <Row gutter={8}>
                  <Col span={12}>
                    <Button
                      onClick={resetState}
                      disabled={loading}
                      icon={<ReloadOutlined />}
                      block
                    >
                      重置
                    </Button>
                  </Col>
                  <Col span={12}>
                    <Button
                      onClick={() => setShowAuditDetails(!showAuditDetails)}
                      icon={<EyeOutlined />}
                      block
                    >
                      {showAuditDetails ? '隐藏详情' : '状态详情'}
                    </Button>
                  </Col>
                </Row>
              </div>
            </div>
          )}

          {/* 审计组状态详情 */}
          {showAuditDetails && taskStatus && renderAgentStatusPanel()}
        </div>

        {/* 右侧栏：评阅结果与评分 */}
        <div className="right-panel">
          {reviewResults ? (

            <>
              {/* <Card title="论文信息" style={{ marginBottom: 20 }}>
                <Descriptions column={2}>
                  <Descriptions.Item label="论文ID">
                    <Tag color="blue">{reviewResults.paper_id}</Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="论文标题">
                    {reviewResults.paper_title}
                  </Descriptions.Item>
                  <Descriptions.Item label="综合评分">
                    {reviewResults.overall_score} 分
                  </Descriptions.Item>
                  <Descriptions.Item label="处理时间">
                    {new Date(reviewResults.generated_at).toLocaleString()}
                  </Descriptions.Item>
                </Descriptions>
              </Card> */}
              {/* 综合评分展示区 */}
              <Card title="综合质量评分" className="score-card">
                <div className="score-display">
                  <div className="score-value">{reviewResults.overall_score}</div>
                  <div className="score-grade">
                    <Tag color={
                      reviewResults.overall_score >= 80 ? 'success' :
                      reviewResults.overall_score >= 60 ? 'warning' : 'error'
                    }>
                      {reviewResults.grade}
                    </Tag>
                  </div>
                  <div className="score-conclusion">{reviewResults.conclusion}</div>
                  {reviewResults.verdict && (
                    <div style={{ marginTop: 12, textAlign: 'left', color: '#595959', fontSize: 14 }}>
                      <strong>反思评估结论：</strong>
                      {reviewResults.verdict}
                    </div>
                  )}
                </div>
                <Progress
                  percent={reviewResults.overall_score}
                  status="active"
                  strokeColor={{
                    '0%': '#108ee9',
                    '100%': '#87d068',
                  }}
                />

                {/* 审计组评分概览 */}
                {reviewResults.group_results && reviewResults.group_results.length > 0 && (
                  <div style={{ marginTop: 20 }}>
                    <h4>审计组评分详情</h4>
                    <div className="review-groups">
                      {reviewResults.group_results.map((group, index) => {
                        const groupType = getGroupTypeFromName(group.group_name);
                        const config = agentConfigs[groupType] || {};
                        return (
                          <div key={index} className="group-card">
                            <Card size="small">
                              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                  {config.icon || <CheckCircleOutlined />}
                                  <span>{config.name || group.group_name}</span>
                                </div>
                                <div>
                                  {group.audit_results?.[0]?.level && (
                                    <Tag color={levelColors[group.audit_results[0].level] || '#d9d9d9'}>
                                      {group.audit_results[0].level}
                                    </Tag>
                                  )}
                                  <span style={{ marginLeft: 8, fontWeight: 600 }}>
                                    {group.audit_results?.[0]?.score || 0}分
                                  </span>
                                </div>
                              </div>
                            </Card>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </Card>

              {/* 智能体评阅矩阵 */}
              <Card title="智能体评阅矩阵" className="review-matrix">
                <div className="review-items">
                  {reviewResults.review_items.map((item, index) => (
                    <div key={index} className={`review-item ${item.status.toLowerCase()}`}>
                      <div className="review-header">
                        <span className="review-title">
                          {agentConfigs[item.category]?.icon || <CheckCircleOutlined />}
                          {item.title}
                        </span>
                        <span className={`review-score ${item.status.toLowerCase()}`}>
                          {item.score}分 / {item.status}
                        </span>
                      </div>
                      <div className="review-desc">{item.description}</div>
                    </div>
                  ))}
                </div>
              </Card>
            </>
          ) : (
            /* 等待评审状态 */
            <Card title="等待评审" className="waiting-card">
              <div className="waiting-content">
                {uploadedFile ? (
                  paperContent ? (
                    <div className="ready-for-review">
                      <Alert
                        message="文件准备就绪"
                        description={
                          <div>
                            {isTestMode ? (
                              <>
                                <p><ExperimentOutlined style={{ color: '#722ed1' }} /> 已加载 <code>md_test</code> 测试正文（未走上传/转 MD）</p>
                                <p><FileTextOutlined /> 虚拟文件名: {uploadedFile}</p>
                              </>
                            ) : (
                              <p><CheckCircleOutlined style={{ color: '#52c41a' }} /> PDF文件已上传并成功转换为Markdown</p>
                            )}
                            {!isTestMode && (
                              <>
                                <p><FilePdfOutlined /> 文件名: {uploadedFile}</p>
                                <p><LineChartOutlined /> 文件大小: {(fileSize / 1024 / 1024).toFixed(2)}MB</p>
                              </>
                            )}
                            <p><FileTextOutlined /> 正文字符数: {paperContent.length}</p>
                            <p style={{ marginTop: 20, fontWeight: 500 }}>
                              <PlayCircleOutlined style={{ color: '#1890ff' }} /> 点击「开始多智能体协同评审」启动完整评审流程
                            </p>
                          </div>
                        }
                        type="success"
                        showIcon
                      />

                      <div className="quick-stats" style={{ marginTop: 20 }}>
                        {[
                          ...(isTestMode ? [] : [{ label: '文件大小 (MB)', value: (fileSize / 1024 / 1024).toFixed(2) }]),
                          { label: '字符数', value: paperContent.length },
                          { label: '行数', value: paperContent.split('\n').length }
                        ].map((stat, index) => (
                          <div className="stat-item" key={index}>
                            <div className="stat-value">{stat.value}</div>
                            <div className="stat-label">{stat.label}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="converting-state">
                      {converting ? (
                        <div className="converting-in-progress">
                          <Spin size="large" tip="正在转换PDF文件..." />
                          <Progress
                            percent={reviewProgress}
                            status="active"
                            strokeColor="#1890ff"
                            style={{ width: '80%', marginTop: 20 }}
                          />
                        </div>
                      ) : (
                        <Alert
                          message="等待转换完成"
                          description="PDF文件已上传，正在等待转换处理..."
                          type="info"
                          showIcon
                        />
                      )}
                    </div>
                  )
                ) : (
                  <div className="no-file-state">
                    <div className="upload-prompt">
                      <CloudUploadOutlined style={{ fontSize: 48, color: '#1890ff', marginBottom: 20 }} />
                      <h3>请上传PDF论文文件</h3>
                      <p>点击上方区域或手动选择PDF文件开始处理</p>
                      <p>系统将自动转换为Markdown格式并进行多智能体协同评审</p>
                    </div>
                  </div>
                )}

                {/* 全局加载状态 */}
                {(loading || converting) && (
                  <div className="loading-overlay">
                    <Spin size="large" />
                    <div className="loading-text">
                      {loading ? '处理中...' : converting ? '转换中...' : '处理中...'}
                    </div>
                  </div>
                )}
              </div>
            </Card>
          )}
        </div>
      </div>

      {/* 反思评估报告：全宽独立区块，左/右两栏与上方「论文区 | 评分区」并列占满视口宽度 */}
      {reviewResults?.reflection && (
        <div className="reflection-report-section">
          <Card
            title="反思评估报告"
            className="reflection-report-card"
            extra={
              reviewResults.paper_id ? (
                <Button
                  type="primary"
                  ghost
                  icon={<DownloadOutlined />}
                  loading={reportDownloadLoading}
                  onClick={downloadReflectionReport}
                >
                  下载评估报告
                </Button>
              ) : null
            }
          >
            {reviewResults.needs_human_review && (
              <Alert
                type="warning"
                showIcon
                message="建议人工复核"
                description={reviewResults.human_review_reason || '系统标记需人工复核'}
                style={{ marginBottom: 16 }}
              />
            )}
            {!hasMentorComments && reflectionDetailRows.length === 0 ? (
              <Alert type="info" showIcon message="本轮反思评估未产生分级问题条目（或均为空）" />
            ) : (
              <Row gutter={[16, 16]}>
                {hasMentorComments ? (
                  <>
                    <Col xs={24} lg={12}>
                      <Card size="small" title="导师评语" className="mentor-dialogue-card">
                        {reviewResults.reflection.mentor_dialogue.conversation.map((turn, idx) => (
                          <Paragraph key={idx} style={{ whiteSpace: 'pre-wrap', marginBottom: 12 }}>
                            {(turn.role === 'mentor' ? '导师：' : `${turn.role || '导师'}：`)}
                            {turn.content}
                          </Paragraph>
                        ))}
                      </Card>
                    </Col>
                    <Col xs={24} lg={12}>
                      <Card size="small" title="问题列表">
                        {allIssuesOrdered.length > 0 ? (
                          <List
                            size="small"
                            dataSource={allIssuesOrdered}
                            renderItem={renderReflectionListItem}
                            locale={{ emptyText: '暂无问题' }}
                          />
                        ) : (
                          <Alert type="info" showIcon message="暂无问题条目" />
                        )}
                      </Card>
                    </Col>
                  </>
                ) : (
                  <>
                    <Col xs={24} lg={12}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                        <Card size="small" title="严重问题">
                          {critIssues.length > 0 ? (
                            <List
                              size="small"
                              dataSource={critIssues}
                              renderItem={renderReflectionListItem}
                              locale={{ emptyText: '暂无' }}
                            />
                          ) : (
                            <Alert type="info" showIcon message="暂无严重问题" />
                          )}
                        </Card>
                        <Card size="small" title="主要问题">
                          {majorIssues.length > 0 ? (
                            <List
                              size="small"
                              dataSource={majorIssues}
                              renderItem={renderReflectionListItem}
                              locale={{ emptyText: '暂无' }}
                            />
                          ) : (
                            <Alert type="info" showIcon message="暂无主要问题" />
                          )}
                        </Card>
                      </div>
                    </Col>
                    <Col xs={24} lg={12}>
                      <Card size="small" title="次要问题">
                        {minorIssues.length > 0 ? (
                          <List
                            size="small"
                            dataSource={minorIssues}
                            renderItem={renderReflectionListItem}
                            locale={{ emptyText: '暂无' }}
                          />
                        ) : (
                          <Alert type="info" showIcon message="暂无次要问题" />
                        )}
                      </Card>
                    </Col>
                  </>
                )}
              </Row>
            )}
          </Card>
        </div>
      )}

      {/* 帮助模态框 */}
      <Modal
        title="系统使用帮助"
        open={showHelp}
        onCancel={() => setShowHelp(false)}
        footer={[
          <Button key="close" onClick={() => setShowHelp(false)}>
            关闭
          </Button>
        ]}
        width={700}
      >
        <div className="help-content">
          <h3>系统架构</h3>
          <p>本系统采用调度器协调四个智能体进行论文评审：</p>
          <ul>
            <li><strong>逻辑审计组</strong>: 检查逻辑一致性、矛盾点、论证链条完整性</li>
            <li><strong>实验数据审计组</strong>: 检查实验设计、数据显著性、结果可复现性</li>
            <li><strong>格式审计组</strong>: 检查论文格式、图表编号、参考文献格式</li>
            <li><strong>文献审计组</strong>: 检查参考文献真实性、相关性、时效性</li>
          </ul>

          <h3>使用流程</h3>
          <ol>
            <li>上传硕士论文PDF文件（最大200MB）</li>
            <li>系统自动将PDF转换为Markdown格式</li>
            <li>提交到调度器，开始多智能体协同评审</li>
            <li>查看各审计组评分和详细评审意见</li>
            <li>下载完整的评审报告</li>
          </ol>

          <h3>API服务</h3>
          <ul>
            <li>PDF上传API: http://localhost:5000</li>
            <li>PDF转Markdown API: http://localhost:8002</li>
            <li>调度器(Orchestrator): http://localhost:7860</li>
            <li>反思评估独立服务（健康检查）: http://localhost:8009</li>
            <li>四审计 Agent: 文献8005 / 实验8006 / 格式8007 / 逻辑8008</li>
            <li>前端应用: http://localhost:3000（开发环境也可用 https://localhost:3000）</li>
          </ul>
          <h3>开发环境说明</h3>
          <p>
            使用 <strong>https://localhost:3000</strong> 时，浏览器会拦截页面去请求 <strong>http://localhost:7860</strong>（混合内容）。
            本项目在开发模式下通过 <code>src/setupProxy.js</code> 将请求代理到各后端，前端实际访问的是同源路径（如 <code>/proxy/orchestrator</code>），无需改后端 CORS。
          </p>
          <p>
            若仍出现「提交失败」或 <code>ERR_CONNECTION_REFUSED</code>，请先在本机启动调度器：<code>cd orchestrator &amp;&amp; python orchestrator.py</code>，并用 <code>start.sh</code> 或分别启动各服务。
          </p>
        </div>
      </Modal>
    </div>
  );
};

export default App;
