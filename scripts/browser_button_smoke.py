#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_SUMMARY_FILE = ROOT_DIR / "build" / "e2e_flow" / "summary.json"
DEFAULT_OUTPUT_JSON = ROOT_DIR / "build" / "browser_button_smoke.json"
DEFAULT_OUTPUT_MD = ROOT_DIR / "build" / "browser_button_smoke.md"
DEFAULT_ARTIFACT_DIR = ROOT_DIR / "output" / "playwright" / "browser_button_smoke"
SYSTEM_CHROME_EXECUTABLE = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
LEXICON_PATH = ROOT_DIR / "app" / "resources" / "lexicon.yaml"
RUBRIC_PATH = ROOT_DIR / "app" / "resources" / "rubric.yaml"

SMOKE_PAGE_HELPERS = """() => {
  window.__codexSmokeState = window.__codexSmokeState || {
    alerts: [],
    prompts: [],
    confirms: [],
    clipboardWrites: [],
  };
  window.alert = (message) => {
    window.__codexSmokeState.alerts.push(String(message || ''));
  };
  window.prompt = (message, value = '') => {
    window.__codexSmokeState.prompts.push({
      message: String(message || ''),
      value: String(value || ''),
    });
    return String(value || '');
  };
  window.confirm = (message) => {
    window.__codexSmokeState.confirms.push(String(message || ''));
    return true;
  };
  const clipboardStub = {
    writeText(text) {
      window.__codexSmokeState.clipboardWrites.push(String(text || ''));
      return Promise.resolve();
    },
  };
  try {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: clipboardStub,
    });
  } catch (error) {
    try {
      navigator.clipboard = clipboardStub;
    } catch (nestedError) {
      window.__codexSmokeState.clipboardInstallError = String(
        (nestedError && nestedError.message) || nestedError || error || ''
      );
    }
  }
  window.__codexRevealForSmoke = window.__codexRevealForSmoke || function(selector) {
    const el = document.querySelector(selector);
    if (!el) return false;
    let node = el;
    while (node) {
      if (node.tagName === 'DETAILS') node.open = true;
      if (node.classList && node.classList.contains('compact-hidden')) {
        node.classList.remove('compact-hidden');
      }
      if (node.id === 'section-adaptive') {
        node.style.display = 'block';
      } else if (node.style && node.style.display === 'none') {
        node.style.display = '';
      }
      node = node.parentElement;
    }
    if (el.style && el.style.display === 'none') {
      el.style.display = '';
    }
    return true;
  };
  window.__codexPrimeClickForSmoke = window.__codexPrimeClickForSmoke || function(selector) {
    if (!window.__codexRevealForSmoke(selector)) return false;
    const el = document.querySelector(selector);
    if (!el) return false;
    el.scrollIntoView({ block: 'center' });
    el.click();
    return true;
  };
  window.__codexInstallFetchLogForSmoke = window.__codexInstallFetchLogForSmoke || function() {
    if (window.__codexSmokeState.fetchLogInstalled) return true;
    window.__codexSmokeState.fetchUrls = Array.isArray(window.__codexSmokeState.fetchUrls)
      ? window.__codexSmokeState.fetchUrls
      : [];
    const originalFetch = window.fetch.bind(window);
    window.fetch = function(...args) {
      const req = args.length ? args[0] : '';
      const url = typeof req === 'string' ? req : String((req && req.url) || '');
      window.__codexSmokeState.fetchUrls.push(String(url || ''));
      return originalFetch(...args);
    };
    window.__codexSmokeState.fetchLogInstalled = true;
    return true;
  };
  window.__codexResetFetchLogForSmoke = window.__codexResetFetchLogForSmoke || function() {
    window.__codexSmokeState.fetchUrls = [];
    return true;
  };
  window.__codexCountFetchesForSmoke = window.__codexCountFetchesForSmoke || function(includes, excludes) {
    const includeNeedle = String(includes || '');
    const excludeNeedle = String(excludes || '');
    const rows = Array.isArray(window.__codexSmokeState.fetchUrls) ? window.__codexSmokeState.fetchUrls : [];
    return rows.filter((url) => {
      const raw = String(url || '');
      if (includeNeedle && !raw.includes(includeNeedle)) return false;
      if (excludeNeedle && raw.includes(excludeNeedle)) return false;
      return true;
    }).length;
  };
  window.__codexSetApiKeyForSmoke = window.__codexSetApiKeyForSmoke || function(value) {
    const normalized = String(value || '').trim();
    try {
      localStorage.setItem('api_key', normalized);
    } catch (_) {}
    const input = document.getElementById('apiKeyInput');
    if (input) input.value = normalized;
    if (typeof window.syncApiKeyHiddenInputs === 'function') {
      try { window.syncApiKeyHiddenInputs(); } catch (_) {}
    }
    return !!normalized;
  };
  window.__codexPrepareUiProjectCreateForSmoke = window.__codexPrepareUiProjectCreateForSmoke || async function(prefix = 'E2E_UI_') {
    const normalizedPrefix = String(prefix || 'E2E_UI_').trim() || 'E2E_UI_';
    const name = normalizedPrefix + String(Date.now());
    if (typeof window.startNewProjectIntake === 'function') {
      try { await window.startNewProjectIntake(); } catch (_) {}
    }
    const input = document.getElementById('createProjectNameInput');
    if (!input) return false;
    if (document.activeElement && document.activeElement !== input && typeof input.focus === 'function') {
      try { input.focus(); } catch (_) {}
    }
    input.value = name;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    window.__codexSmokeState.uiCreatedProjectName = name;
    window.__codexSmokeState.uiCreatedProjectId = '';
    return true;
  };
  window.__codexPrepareUiProjectCreateFromTenderForSmoke = window.__codexPrepareUiProjectCreateFromTenderForSmoke || async function(prefix = 'E2E_TENDER_') {
    const normalizedPrefix = String(prefix || 'E2E_TENDER_').trim() || 'E2E_TENDER_';
    const name = normalizedPrefix + String(Date.now());
    if (typeof window.startNewProjectIntake === 'function') {
      try { await window.startNewProjectIntake(); } catch (_) {}
    }
    const input = document.getElementById('createProjectNameInput');
    if (!input) return false;
    input.value = name;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    const fileInput = document.getElementById('createProjectFromTenderFile');
    if (fileInput) {
      try { fileInput.value = ''; } catch (_) {}
    }
    const message = document.getElementById('createProjectMessage');
    if (message) message.textContent = '';
    const output = document.getElementById('output');
    if (output) output.textContent = '';
    window.__codexSmokeState.uiTenderCreatedProjectName = name;
    window.__codexSmokeState.uiTenderCreatedProjectId = '';
    return true;
  };
  window.__codexPrepareBatchDeleteProjectsForSmoke = window.__codexPrepareBatchDeleteProjectsForSmoke || async function(prefix = 'E2E_BULK_DELETE_') {
    const apiKey = String(
      ((window.__codexSmokeState && window.__codexSmokeState.adminApiKey) || localStorage.getItem('api_key') || '')
    ).trim();
    if (!apiKey) return false;
    const normalizedPrefix = String(prefix || 'E2E_BULK_DELETE_').trim() || 'E2E_BULK_DELETE_';
    const select = document.getElementById('projectDeleteSelect');
    if (!select) return false;
    const created = [];
    for (let index = 0; index < 2; index += 1) {
      const name = normalizedPrefix + String(Date.now()) + '_' + String(index + 1);
      let response;
      try {
        response = await fetch('/api/v1/projects', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-API-Key': apiKey,
          },
          body: JSON.stringify({ name }),
        });
      } catch (_) {
        return false;
      }
      if (!response || !response.ok) return false;
      const row = await response.json().catch(() => ({}));
      const projectId = String((row && row.id) || '').trim();
      const projectName = String((row && row.name) || name).trim();
      if (!projectId) return false;
      created.push({ id: projectId, name: projectName });
    }
    select.innerHTML = '';
    created.forEach((row) => {
      const option = document.createElement('option');
      option.value = row.id;
      option.textContent = row.name;
      option.selected = true;
      select.appendChild(option);
    });
    select.disabled = false;
    const deleteButton = document.getElementById('deleteSelectedProjects');
    if (deleteButton) deleteButton.disabled = false;
    const output = document.getElementById('output');
    if (output) output.textContent = '';
    const status = document.getElementById('selectProjectMessage');
    if (status) status.textContent = '';
    window.__codexSmokeState.batchDeleteProjectIds = created.map((row) => String(row.id || ''));
    window.__codexSmokeState.batchDeleteProjectNames = created.map((row) => String(row.name || ''));
    return created.length === 2;
  };
  window.__codexActivateTrialPreflightFollowUpForSmoke = window.__codexActivateTrialPreflightFollowUpForSmoke || async function() {
    const deadline = Date.now() + 30000;
    const actionVisible = () => {
      const actionEl = document.getElementById('materialsTrialPreflightFollowUpAction');
      if (!actionEl) return false;
      if (window.getComputedStyle(actionEl).display === 'none') return false;
      return !!String(actionEl.textContent || '').trim();
    };
    if (actionVisible()) return true;
    const trigger = document.getElementById('btnTrialPreflight');
    if (!trigger) return false;
    if (typeof window.__codexRevealForSmoke === 'function') {
      try { window.__codexRevealForSmoke('#btnTrialPreflight'); } catch (_) {}
    }
    try { trigger.click(); } catch (_) {}
    while (Date.now() < deadline) {
      const panel = document.getElementById('trialPreflightResult');
      const panelVisible = !!panel && window.getComputedStyle(panel).display !== 'none';
      const reviewLink = panel && typeof panel.querySelector === 'function'
        ? panel.querySelector('a[data-trial-preflight-entry="material_review"]')
        : null;
      if (panelVisible && reviewLink) {
        try { reviewLink.click(); } catch (_) {}
      }
      if (actionVisible()) return true;
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    return false;
  };
  window.__codexWaitForScoringReady = window.__codexWaitForScoringReady || async function(projectId, timeoutMs = 30000) {
    const id = String(projectId || '').trim();
    if (!id) return false;
    const deadline = Date.now() + Math.max(1000, Number(timeoutMs || 0));
    while (Date.now() < deadline) {
      try {
        const res = await fetch('/api/v1/projects/' + encodeURIComponent(id) + '/scoring_readiness?t=' + Date.now(), {
          cache: 'no-store',
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data && data.ready) return true;
      } catch (_) {}
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    return false;
  };
  window.__codexSetWeightSliderForSmoke = window.__codexSetWeightSliderForSmoke || function(dimId, value) {
    const normalizedDim = String(dimId || '').padStart(2, '0');
    const input = document.getElementById('w_' + normalizedDim);
    if (!input) return false;
    input.value = String(value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  };
  window.__codexFillGroundTruthDraftForSmoke = window.__codexFillGroundTruthDraftForSmoke || async function(scores) {
    const judgeScores = Array.isArray(scores) && scores.length ? scores : [84, 84, 84, 84, 84];
    const select = document.getElementById('groundTruthSubmissionSelect');
    if (!select) return false;
    const currentProjectId = (() => {
      const projectSelect = document.getElementById('projectSelect');
      const selectedProjectId = String((projectSelect && projectSelect.value) || '').trim();
      if (selectedProjectId) return selectedProjectId;
      return String(new URL(window.location.href).searchParams.get('project_id') || '').trim();
    })();
    if (!currentProjectId) return false;
    let submissions = [];
    let groundTruthRows = [];
    try {
      const base = '/api/v1/projects/' + encodeURIComponent(currentProjectId);
      const [submissionsRes, groundTruthRes] = await Promise.all([
        fetch(base + '/submissions?t=' + Date.now(), { cache: 'no-store' }),
        fetch(base + '/ground_truth?t=' + Date.now(), { cache: 'no-store' }),
      ]);
      if (!submissionsRes.ok || !groundTruthRes.ok) return false;
      submissions = await submissionsRes.json().catch(() => []);
      groundTruthRows = await groundTruthRes.json().catch(() => []);
    } catch (_) {
      return false;
    }
    const existingSubmissionIds = new Set(
      (Array.isArray(groundTruthRows) ? groundTruthRows : [])
        .map((row) => String((row && (row.source_submission_id || row.submission_id || '')) || '').trim())
        .filter(Boolean)
    );
    const rows = Array.isArray(submissions) ? submissions : [];
    select.innerHTML = '';
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = rows.length ? '-- 请选择步骤4施组 --' : '-- 暂无可用施组 --';
    select.appendChild(placeholder);
    let preferredSubmissionId = '';
    let fallbackSubmissionId = '';
    for (const row of rows) {
      const submissionId = String((row && row.id) || '').trim();
      if (!submissionId) continue;
      const option = document.createElement('option');
      option.value = submissionId;
      option.textContent = String((row && (row.filename || row.name || submissionId)) || submissionId);
      select.appendChild(option);
      fallbackSubmissionId = submissionId;
      if (!preferredSubmissionId && !existingSubmissionIds.has(submissionId)) {
        preferredSubmissionId = submissionId;
      }
    }
    const selectedSubmissionId = preferredSubmissionId || fallbackSubmissionId;
    if (!selectedSubmissionId) return false;
    select.value = selectedSubmissionId;
    select.dispatchEvent(new Event('input', { bubbles: true }));
    select.dispatchEvent(new Event('change', { bubbles: true }));
    const judgeCount = document.getElementById('gtJudgeCount');
    if (judgeCount) {
      judgeCount.value = '5';
      judgeCount.dispatchEvent(new Event('input', { bubbles: true }));
      judgeCount.dispatchEvent(new Event('change', { bubbles: true }));
    }
    for (let idx = 0; idx < 5; idx += 1) {
      const input = document.getElementById('gtJ' + String(idx + 1));
      if (!input) continue;
      input.value = String(judgeScores[idx] != null ? judgeScores[idx] : 84);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (typeof window.applyGroundTruthFinalScoreAutoFill === 'function') {
      try { window.applyGroundTruthFinalScoreAutoFill(); } catch (_) {}
    }
    const finalInput = document.getElementById('gtFinal');
    if (!finalInput) return false;
    const finalDeadline = Date.now() + 2000;
    while (Date.now() < finalDeadline) {
      if (String(finalInput.value || '').trim()) return true;
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
    const avg = judgeScores.reduce((sum, value) => sum + Number(value || 0), 0) / judgeScores.length;
    finalInput.value = String(Number(avg.toFixed(2)));
    finalInput.dispatchEvent(new Event('input', { bubbles: true }));
    finalInput.dispatchEvent(new Event('change', { bubbles: true }));
    return !!String(finalInput.value || '').trim();
  };
  window.__codexForceProjectContextForSmoke = window.__codexForceProjectContextForSmoke || async function(projectId, projectName) {
    const normalizedId = String(projectId || '').trim();
    if (!normalizedId) return false;
    const normalizedName = String(projectName || '').trim() || ('Smoke_' + normalizedId.slice(0, 8));
    const select = document.getElementById('projectSelect');
    if (!select) return false;
    let option = Array.from(select.options || []).find((item) => String(item.value || '') === normalizedId);
    if (!option) {
      option = document.createElement('option');
      option.value = normalizedId;
      option.textContent = normalizedName;
      option.dataset.projectName = normalizedName;
      select.appendChild(option);
    }
    select.value = normalizedId;
    try {
      localStorage.setItem('selected_project_id', normalizedId);
    } catch (_) {}
    if (typeof window.syncProjectHiddenInputs === 'function') {
      try { window.syncProjectHiddenInputs(normalizedId); } catch (_) {}
    }
    if (typeof window.syncProjectSelectionUrl === 'function') {
      try { window.syncProjectSelectionUrl(normalizedId); } catch (_) {}
    }
    if (typeof window.applyProjectScoreScale === 'function') {
      try { window.applyProjectScoreScale(normalizedId); } catch (_) {}
    }
    if (typeof window.updateProjectBoundControlsState === 'function') {
      try { window.updateProjectBoundControlsState(); } catch (_) {}
    }
    if (typeof window.refreshSubmissions === 'function') {
      try { await window.refreshSubmissions(normalizedId); } catch (_) {}
    }
    if (typeof window.refreshMaterials === 'function') {
      try { await window.refreshMaterials(normalizedId, null, { skipReadinessRefresh: true }); } catch (_) {}
    }
    if (typeof window.refreshScoringReadiness === 'function') {
      try { await window.refreshScoringReadiness(normalizedId); } catch (_) {}
    }
    if (typeof window.refreshScoringDiagnostic === 'function') {
      try { await window.refreshScoringDiagnostic(normalizedId); } catch (_) {}
    }
    if (typeof window.refreshGroundTruth === 'function') {
      try { await window.refreshGroundTruth(normalizedId); } catch (_) {}
    }
    if (typeof window.refreshGroundTruthSubmissionOptions === 'function') {
      try { await window.refreshGroundTruthSubmissionOptions(normalizedId, null, undefined, { forceFetch: true }); } catch (_) {}
    }
    if (typeof window.loadExpertProfile === 'function') {
      try { await window.loadExpertProfile(normalizedId); } catch (_) {}
    }
    return String((document.getElementById('projectSelect') || {}).value || '') === normalizedId;
  };
}"""

BUTTON_SMOKE_MATRIX: List[Dict[str, Any]] = [
    {
        "id": "btnSaveApiKey",
        "label": "保存 API Key",
        "selector": "#btnSaveApiKey",
        "kind": "js_check",
        "prepare_js": """(async () => {
          window.__codexRevealForSmoke('#btnSaveApiKey');
          const state = window.__codexSmokeState || {};
          const input = document.getElementById('apiKeyInput');
          const tag = document.getElementById('authStatusTag');
          const value = String(state.adminApiKey || '').trim();
          try { localStorage.removeItem('api_key'); } catch (_) {}
          if (tag) tag.textContent = '';
          if (!input || !value) return false;
          input.value = value;
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
          return true;
        })()""",
        "verify_js": """() => {
          const input = document.getElementById('apiKeyInput');
          const tag = document.getElementById('authStatusTag');
          const stored = String(localStorage.getItem('api_key') || '').trim();
          return !!stored
            && stored === String((input && input.value) || '').trim()
            && String((tag && tag.textContent) || '').includes('API Key 校验通过，已保存');
        }""",
        "observed_js": """() => {
          const tag = document.getElementById('authStatusTag');
          return String((tag && tag.textContent) || '');
        }""",
        "timeout_ms": 30000,
    },
    {
        "id": "btnClearApiKey",
        "label": "清空 API Key",
        "selector": "#btnClearApiKey",
        "kind": "js_check",
        "requires_api_key": True,
        "prepare_js": """(() => {
          window.__codexRevealForSmoke('#btnClearApiKey');
          const tag = document.getElementById('authStatusTag');
          if (tag) tag.textContent = '';
          return true;
        })()""",
        "verify_js": """() => {
          const input = document.getElementById('apiKeyInput');
          const stored = String(localStorage.getItem('api_key') || '').trim();
          return !stored
            && !String((input && input.value) || '').trim();
        }""",
        "observed_js": """() => {
          const tag = document.getElementById('authStatusTag');
          const input = document.getElementById('apiKeyInput');
          return JSON.stringify({
            stored: String(localStorage.getItem('api_key') || ''),
            input: String((input && input.value) || ''),
            tag: String((tag && tag.textContent) || ''),
          });
        }""",
    },
    {
        "id": "btnWeightsReset",
        "label": "重置默认(全部=5)",
        "selector": "#btnWeightsReset",
        "kind": "js_check",
        "prepare_js": """() => window.__codexSetWeightSliderForSmoke('09', 7)""",
        "verify_js": """() => {
          const input = document.getElementById('w_09');
          if (!input) return false;
          return String(input.value || '') === '5';
        }""",
        "observed_js": """() => {
          const summary = document.getElementById('expertWeightsSummary');
          return String((summary && summary.textContent) || '');
        }""",
    },
    {
        "id": "btnOptimizationReport",
        "label": "满分优化清单（主按钮）",
        "selector": "#btnOptimizationReport",
        "kind": "result",
        "result_id": "compareReportResult",
        "expected_text": "逐份施组得分项/失分项（按文件）",
    },
    {
        "id": "btnCompareReportRow",
        "label": "满分优化清单（行内按钮）",
        "selector": "#submissionsTable .js-open-compare-report",
        "kind": "result",
        "result_id": "compareReportResult",
        "expected_text": "当前仅分析你点击的这一份施组，不混入其它文件的优化建议。",
    },
    {
        "id": "btnCompareReport",
        "label": "满分优化清单（逐页）",
        "selector": "#btnCompareReport",
        "kind": "result",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnCompareReport')""",
        "result_id": "compareReportResult",
        "expected_text": "逐份施组得分项/失分项（按文件）",
    },
    {
        "id": "btnScoringFactors",
        "label": "评分体系一览",
        "selector": "#btnScoringFactors",
        "kind": "result",
        "result_id": "scoringFactorsResult",
        "expected_text": "评分体系总览已加载",
    },
    {
        "id": "btnScoringFactorsMd",
        "label": "评分体系Markdown",
        "selector": "#btnScoringFactorsMd",
        "kind": "result",
        "result_id": "scoringFactorsResult",
        "expected_text": "评分体系 Markdown 已生成",
    },
    {
        "id": "btnCompare",
        "label": "对比排名",
        "selector": "#btnCompare",
        "kind": "result",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnCompare')""",
        "result_id": "compareResult",
        "expected_text": "当前总分(优先当前分)",
    },
    {
        "id": "btnInsights",
        "label": "洞察",
        "selector": "#btnInsights",
        "kind": "result",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnInsights')""",
        "result_id": "insightsResult",
        "expected_text": "弱项维度",
    },
    {
        "id": "btnEvidenceTrace",
        "label": "证据追溯（最新施组）",
        "selector": "#btnEvidenceTrace",
        "kind": "result",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnEvidenceTrace')""",
        "result_id": "evidenceTraceResult",
        "expected_text": "证据追溯（最新施组）",
    },
    {
        "id": "btnEvidenceTraceDownload",
        "label": "证据追溯结果区下载 Markdown",
        "selector": "#btnEvidenceTraceDownload",
        "kind": "download",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnEvidenceTrace')""",
        "prime_js": """() => window.__codexPrimeClickForSmoke('#btnEvidenceTrace')""",
        "prime_wait_selector": "#btnEvidenceTraceDownload",
        "expected_filename_prefix": "evidence_trace_",
        "expected_filename_suffix": ".md",
    },
    {
        "id": "btnScoringBasis",
        "label": "评分依据（最新施组）",
        "selector": "#btnScoringBasis",
        "kind": "result",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnScoringBasis')""",
        "result_id": "scoringBasisResult",
        "expected_text": "评分依据审计（最新施组）",
    },
    {
        "id": "btnScoringDiagnostic",
        "label": "评分证据链诊断",
        "selector": "#btnScoringDiagnostic",
        "kind": "result",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnScoringDiagnostic')""",
        "result_id": "scoringDiagnosticResult",
        "expected_text": "评分证据链诊断（项目级）",
        "expected_output_text": "评分证据链诊断已刷新。",
        "verify_js": """() => {
          const el = document.getElementById('scoringDiagnosticResult');
          if (!el) return false;
          const display = window.getComputedStyle(el).display;
          if (display === 'none') return false;
          const text = String(el.textContent || '');
          return text.includes('评分证据链诊断（项目级）')
            && !text.includes('评分证据链诊断生成中...');
        }""",
    },
    {
        "id": "btnMaterialDepthReport",
        "label": "资料深读体检",
        "selector": "#btnMaterialDepthReport",
        "kind": "result",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnMaterialDepthReport')""",
        "result_id": "materialDepthReportResult",
        "expected_text": "资料深读体检（评分前）",
    },
    {
        "id": "btnMaterialDepthReportDownload",
        "label": "下载体检报告(.md)",
        "selector": "#btnMaterialDepthReportDownload",
        "kind": "download",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnMaterialDepthReportDownload')""",
        "expected_filename_prefix": "material_depth_report_",
        "expected_filename_suffix": ".md",
    },
    {
        "id": "btnMaterialKnowledgeProfile",
        "label": "资料知识画像",
        "selector": "#btnMaterialKnowledgeProfile",
        "kind": "result",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnMaterialKnowledgeProfile')""",
        "result_id": "materialKnowledgeProfileResult",
        "expected_text": "资料知识画像（按维度覆盖）",
    },
    {
        "id": "btnMaterialKnowledgeProfileDownload",
        "label": "下载知识画像(.md)",
        "selector": "#btnMaterialKnowledgeProfileDownload",
        "kind": "download",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnMaterialKnowledgeProfileDownload')""",
        "expected_filename_prefix": "material_knowledge_profile_",
        "expected_filename_suffix": ".md",
    },
    {
        "id": "btnAdaptive",
        "label": "自适应建议",
        "selector": "#btnAdaptive",
        "kind": "result",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnAdaptive')""",
        "result_id": "adaptiveResult",
        "expected_text": "扣分统计",
    },
    {
        "id": "btnAdaptivePatch",
        "label": "生成补丁",
        "selector": "#btnAdaptivePatch",
        "kind": "result",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnAdaptivePatch')""",
        "result_id": "adaptivePatchResult",
        "expected_text": "规则补丁",
    },
    {
        "id": "btnAdaptiveValidate",
        "label": "验证效果",
        "selector": "#btnAdaptiveValidate",
        "kind": "result",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnAdaptiveValidate')""",
        "result_id": "adaptiveValidateResult",
        "expected_text": "平均分差（新-旧）",
    },
    {
        "id": "btnAnalysisBundle",
        "label": "项目分析包",
        "selector": "#btnAnalysisBundle",
        "kind": "result",
        "result_id": "scoringFactorsResult",
        "expected_text": "项目分析包已生成",
    },
    {
        "id": "btnAnalysisBundleDownload",
        "label": "下载分析包(.md)",
        "selector": "#btnAnalysisBundleDownload",
        "kind": "download",
        "expected_filename_prefix": "analysis_bundle_",
        "expected_filename_suffix": ".md",
    },
    {
        "id": "btnStartNewProject",
        "label": "开始新项目",
        "selector": "#btnStartNewProject",
        "kind": "js_check",
        "prepare_js": """(() => {
          const state = window.__codexSmokeState || {};
          const select = document.getElementById('projectSelect');
          const currentId = String((select && select.value) || '').trim();
          const currentOption = Array.from((select && select.options) || []).find((item) => String(item.value || '').trim() === currentId);
          state.startNewProjectRestoreId = currentId;
          state.startNewProjectRestoreName = String(
            (currentOption && (currentOption.dataset.projectName || currentOption.textContent)) || currentId
          ).trim();
          state.startNewProjectRestored = false;
          return !!currentId && window.__codexRevealForSmoke('#btnStartNewProject');
        })()""",
        "verify_js": """async () => {
          const state = window.__codexSmokeState || {};
          const select = document.getElementById('projectSelect');
          const intakeMode = String(localStorage.getItem('project_intake_mode') || '') === '1';
          const currentId = String((select && select.value) || '').trim();
          if (!intakeMode || currentId) return false;
          if (state.startNewProjectRestored) return true;
          state.startNewProjectRestored = true;
          const restoreId = String(state.startNewProjectRestoreId || '').trim();
          const restoreName = String(state.startNewProjectRestoreName || restoreId || '').trim();
          if (restoreId && typeof window.__codexForceProjectContextForSmoke === 'function') {
            try { await window.__codexForceProjectContextForSmoke(restoreId, restoreName); } catch (_) {}
          }
          return true;
        }""",
        "observed_js": """() => {
          const createMsg = document.getElementById('createProjectMessage');
          return String((createMsg && createMsg.textContent) || '');
        }""",
        "timeout_ms": 30000,
    },
    {
        "id": "refreshProjects",
        "label": "刷新项目列表",
        "selector": "#refreshProjects",
        "kind": "js_check",
        "prepare_js": """(() => {
          const state = window.__codexSmokeState || {};
          window.__codexRevealForSmoke('#refreshProjects');
          window.__codexInstallFetchLogForSmoke();
          window.__codexResetFetchLogForSmoke();
          state.refreshProjectsNeedle = '/api/v1/projects';
          return true;
        })()""",
        "verify_js": """() => {
          const state = window.__codexSmokeState || {};
          const select = document.getElementById('projectSelect');
          return window.__codexCountFetchesForSmoke(String(state.refreshProjectsNeedle || '')) > 0
            && !!select
            && (select.options || []).length >= 1;
        }""",
        "observed_js": """() => {
          const msg = document.getElementById('projectSelectMsg');
          return String((msg && msg.textContent) || '');
        }""",
        "timeout_ms": 30000,
    },
    {
        "id": "btnSelectProjectBySearch",
        "label": "定位项目",
        "selector": "#btnSelectProjectBySearch",
        "kind": "js_check",
        "prepare_js": """(() => {
          const state = window.__codexSmokeState || {};
          const select = document.getElementById('projectSelect');
          const currentId = String((select && select.value) || '').trim();
          const currentOption = Array.from((select && select.options) || []).find((item) => String(item.value || '').trim() === currentId);
          state.projectSearchRestoreId = currentId;
          state.projectSearchRestoreName = String(
            (currentOption && (currentOption.dataset.projectName || currentOption.textContent)) || currentId
          ).trim();
          const visibleRows = Array.isArray(window.projectVisibleListCache) ? window.projectVisibleListCache : [];
          const matched = visibleRows.find((project) => String((project && project.id) || '').trim() !== currentId)
            || visibleRows.find((project) => String((project && project.id) || '').trim() === currentId);
          const searchInput = document.getElementById('projectSearchInput');
          const keyword = String(
            (matched && (matched.name || matched.id))
            || (currentOption && (currentOption.dataset.projectName || currentOption.textContent))
            || ''
          ).trim();
          if (!searchInput || !keyword) return false;
          searchInput.value = keyword;
          searchInput.dispatchEvent(new Event('input', { bubbles: true }));
          state.projectSearchExpectedId = String((matched && matched.id) || currentId || '').trim();
          state.projectSearchExpectedName = keyword;
          return true;
        })()""",
        "verify_js": """async () => {
          const state = window.__codexSmokeState || {};
          const select = document.getElementById('projectSelect');
          const msg = document.getElementById('projectSelectMsg');
          const currentId = String((select && select.value) || '').trim();
          const expectedId = String(state.projectSearchExpectedId || '').trim();
          if (
            !!currentId
            && currentId === expectedId
            && String((msg && msg.textContent) || '').includes('已定位到项目')
          ) {
            const restoreId = String(state.projectSearchRestoreId || '').trim();
            const restoreName = String(state.projectSearchRestoreName || restoreId || '').trim();
            if (restoreId && restoreId !== currentId && typeof window.__codexForceProjectContextForSmoke === 'function') {
              try { await window.__codexForceProjectContextForSmoke(restoreId, restoreName); } catch (_) {}
              return String(((document.getElementById('projectSelect') || {}).value) || '').trim() === restoreId;
            }
            return true;
          }
          return false;
        }""",
        "observed_js": """() => {
          const msg = document.getElementById('projectSelectMsg');
          return String((msg && msg.textContent) || '');
        }""",
        "timeout_ms": 30000,
    },
    {
        "id": "btnRefreshMaterials",
        "label": "刷新资料",
        "selector": "#btnRefreshMaterials",
        "kind": "js_check",
        "prepare_js": """(() => {
          const state = window.__codexSmokeState || {};
          const projectId = String((((document.getElementById('projectSelect') || {}).value) || '')).trim();
          if (!projectId) return false;
          window.__codexRevealForSmoke('#btnRefreshMaterials');
          window.__codexInstallFetchLogForSmoke();
          window.__codexResetFetchLogForSmoke();
          state.refreshMaterialsNeedle = '/api/v1/projects/' + projectId + '/materials/parse_status';
          return true;
        })()""",
        "verify_js": """() => {
          const state = window.__codexSmokeState || {};
          const table = document.getElementById('materialsTable');
          return window.__codexCountFetchesForSmoke(String(state.refreshMaterialsNeedle || '')) > 0
            && !!table;
        }""",
        "observed_js": """() => {
          const msg = document.getElementById('materialsEmpty');
          return String((msg && msg.textContent) || '');
        }""",
        "timeout_ms": 30000,
    },
    {
        "id": "btnRefreshSubmissions",
        "label": "刷新施组",
        "selector": "#btnRefreshSubmissions",
        "kind": "js_check",
        "prepare_js": """(() => {
          const state = window.__codexSmokeState || {};
          const projectId = String((((document.getElementById('projectSelect') || {}).value) || '')).trim();
          if (!projectId) return false;
          window.__codexRevealForSmoke('#btnRefreshSubmissions');
          window.__codexInstallFetchLogForSmoke();
          window.__codexResetFetchLogForSmoke();
          state.refreshSubmissionsNeedle = '/api/v1/projects/' + projectId + '/submissions';
          return true;
        })()""",
        "verify_js": """() => {
          const state = window.__codexSmokeState || {};
          const table = document.getElementById('submissionsTable');
          return window.__codexCountFetchesForSmoke(String(state.refreshSubmissionsNeedle || '')) > 0
            && !!table;
        }""",
        "observed_js": """() => {
          const msg = document.getElementById('submissionsEmpty');
          return String((msg && msg.textContent) || '');
        }""",
        "timeout_ms": 30000,
    },
    {
        "id": "btnRefreshGroundTruth",
        "label": "刷新真实评标",
        "selector": "#btnRefreshGroundTruth",
        "kind": "result",
        "result_id": "evolveResult",
        "expected_text": "刷新完成：",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnRefreshGroundTruth')""",
    },
    {
        "id": "btnWritingGuidance",
        "label": "查看高分编制指导",
        "selector": "#btnWritingGuidance",
        "kind": "result",
        "result_id": "guidanceResult",
        "expected_text": "高分逻辑",
    },
    {
        "id": "btnGuidancePatchBundleInlineDownloadDocx",
        "label": "编制指导结果区下载改写补丁包(.docx)",
        "selector": "#btnGuidancePatchBundleInlineDownloadDocx",
        "kind": "download",
        "expected_filename_prefix": "writing_guidance_patch_bundle_",
        "expected_filename_suffix": ".docx",
        "optional": True,
    },
    {
        "id": "btnWritingGuidancePatchBundleDownloadDocx",
        "label": "下载改写补丁包(.docx)",
        "selector": "#btnWritingGuidancePatchBundleDownloadDocx",
        "kind": "download",
        "expected_filename_prefix": "writing_guidance_patch_bundle_",
        "expected_filename_suffix": ".docx",
    },
]

WRITE_BUTTON_SMOKE_MATRIX: List[Dict[str, Any]] = [
    {
        "id": "btnScoreShigong",
        "label": "评分施组（E2E写入）",
        "selector": "#btnScoreShigong",
        "kind": "result",
        "result_id": "shigongActionStatus",
        "requires_api_key": True,
        "prepare_js": """(async () => {
          const projectId = String(new URL(window.location.href).searchParams.get('project_id') || '');
          const ready = await window.__codexWaitForScoringReady(projectId, 30000);
          window.__codexSmokeState.scoreReady = !!ready;
          return ready;
        })()""",
        "precondition_js": "!!(window.__codexSmokeState && window.__codexSmokeState.scoreReady)",
        "precondition_optional": True,
        "timeout_ms": 60000,
        "expected_text": "施组评分完成（",
    },
    {
        "id": "btnWeightsSave",
        "label": "保存为专家配置（E2E写入）",
        "selector": "#btnWeightsSave",
        "kind": "result",
        "result_id": "scoringFactorsResult",
        "requires_api_key": True,
        "prepare_js": """(() => {
          window.__codexSetWeightSliderForSmoke('09', 7);
          const result = document.getElementById('scoringFactorsResult');
          if (result) result.textContent = '';
          return window.__codexRevealForSmoke('#btnWeightsSave');
        })()""",
        "expected_text": "专家配置已保存并绑定到当前项目",
    },
    {
        "id": "btnUploadMaterials",
        "label": "上传资料（E2E写入）",
        "selector": "#btnUploadMaterials",
        "kind": "result",
        "result_id": "materialsActionStatus",
        "requires_api_key": True,
        "file_input_selector": "#uploadMaterialFile",
        "input_files": [str(ROOT_DIR / "tests" / "fixtures" / "browser_smoke_tender_qa.txt")],
        "prepare_js": """(() => {
          window.__codexRevealForSmoke('#btnUploadMaterials');
          const result = document.getElementById('materialsActionStatus');
          if (result) result.textContent = '';
          return true;
        })()""",
        "expected_text": "上传完成：成功 1，失败 0",
        "expected_output_text": "招标文件和答疑上传完成：成功 1，失败 0",
    },
    {
        "id": "btnUploadBoq",
        "label": "上传清单（E2E写入）",
        "selector": "#btnUploadBoq",
        "kind": "result",
        "result_id": "materialsActionStatusBoq",
        "requires_api_key": True,
        "file_input_selector": "#uploadMaterialBoqFile",
        "input_files": [str(ROOT_DIR / "tests" / "fixtures" / "browser_smoke_boq.csv")],
        "prepare_js": """(() => {
          window.__codexRevealForSmoke('#btnUploadBoq');
          const result = document.getElementById('materialsActionStatusBoq');
          if (result) result.textContent = '';
          return true;
        })()""",
        "expected_text": "上传完成：成功 1，失败 0",
        "expected_output_text": "清单上传完成：成功 1，失败 0",
    },
    {
        "id": "btnUploadDrawing",
        "label": "上传图纸（E2E写入）",
        "selector": "#btnUploadDrawing",
        "kind": "result",
        "result_id": "materialsActionStatusDrawing",
        "requires_api_key": True,
        "file_input_selector": "#uploadMaterialDrawingFile",
        "input_files": [str(ROOT_DIR / "tests" / "fixtures" / "browser_smoke_drawing.txt")],
        "prepare_js": """(() => {
          window.__codexRevealForSmoke('#btnUploadDrawing');
          const result = document.getElementById('materialsActionStatusDrawing');
          if (result) result.textContent = '';
          return true;
        })()""",
        "expected_text": "上传完成：成功 1，失败 0",
        "expected_output_text": "图纸上传完成：成功 1，失败 0",
    },
    {
        "id": "btnUploadSitePhotos",
        "label": "上传照片（E2E写入）",
        "selector": "#btnUploadSitePhotos",
        "kind": "result",
        "result_id": "materialsActionStatusPhoto",
        "requires_api_key": True,
        "file_input_selector": "#uploadMaterialPhotoFile",
        "input_files": [str(ROOT_DIR / "tests" / "fixtures" / "browser_smoke_site_photo.png")],
        "prepare_js": """(() => {
          window.__codexRevealForSmoke('#btnUploadSitePhotos');
          const result = document.getElementById('materialsActionStatusPhoto');
          if (result) result.textContent = '';
          return true;
        })()""",
        "expected_text": "上传完成：成功 1，失败 0",
        "expected_output_text": "现场照片上传完成：成功 1，失败 0",
    },
    {
        "id": "btnUploadShigong",
        "label": "上传施组（E2E写入）",
        "selector": "#btnUploadShigong",
        "kind": "result",
        "result_id": "shigongActionStatus",
        "requires_api_key": True,
        "file_input_selector": "#uploadShigongFile",
        "input_files": [str(ROOT_DIR / "sample_shigong.txt")],
        "prepare_js": """(() => {
          window.__codexRevealForSmoke('#btnUploadShigong');
          const result = document.getElementById('shigongActionStatus');
          if (result) result.textContent = '';
          return true;
        })()""",
        "expected_text": "施组上传完成：成功 1，失败 0",
    },
    {
        "id": "btnWeightsApply",
        "label": "应用关注度并重算（E2E写入）",
        "selector": "#btnWeightsApply",
        "kind": "result",
        "result_id": "expertProfileStatus",
        "requires_api_key": True,
        "prepare_js": """(async () => {
          window.__codexSetWeightSliderForSmoke('09', 8);
          const status = document.getElementById('expertProfileStatus');
          if (status) status.textContent = '';
          const projectId = String(new URL(window.location.href).searchParams.get('project_id') || '');
          const ready = await window.__codexWaitForScoringReady(projectId, 30000);
          window.__codexSmokeState.weightsApplyReady = !!ready;
          return ready;
        })()""",
        "precondition_js": "!!(window.__codexSmokeState && window.__codexSmokeState.weightsApplyReady)",
        "precondition_optional": True,
        "expected_text": "重算完成（",
    },
    {
        "id": "btnLearning",
        "label": "生成学习画像（E2E写入）",
        "selector": "#btnLearning",
        "kind": "result",
        "prepare_js": """() => window.__codexRevealForSmoke('#btnLearning')""",
        "result_id": "learningResult",
        "expected_text": "学习画像已生成/更新",
    },
    {
        "id": "btnRefreshGroundTruthSubmissionOptions",
        "label": "刷新施组选项（E2E写入准备）",
        "selector": "#btnRefreshGroundTruthSubmissionOptions",
        "kind": "result",
        "result_id": "evolveResult",
        "prepare_js": """(() => window.__codexRevealForSmoke('#btnRefreshGroundTruthSubmissionOptions'))()""",
        "expected_text": "施组选项已刷新：请在下拉框中选择步骤4已上传施组。",
    },
    {
        "id": "btnAddGroundTruth",
        "label": "录入真实评标（E2E写入）",
        "selector": "#btnAddGroundTruth",
        "kind": "result",
        "result_id": "evolveResult",
        "requires_api_key": True,
        "prepare_js": """(async () => {
          window.__codexRevealForSmoke('#btnAddGroundTruth');
          const result = document.getElementById('evolveResult');
          if (result) result.textContent = '';
          const filled = await window.__codexFillGroundTruthDraftForSmoke([84, 84, 84, 84, 84]);
          window.__codexSmokeState.groundTruthDraftReady = !!filled;
          return filled;
        })()""",
        "precondition_js": "!!(window.__codexSmokeState && window.__codexSmokeState.groundTruthDraftReady)",
        "expected_text": "真实评标录入完成：已记录 1 条。",
    },
    {
        "id": "btnEvolve",
        "label": "学习进化（E2E写入）",
        "selector": "#btnEvolve",
        "kind": "result",
        "result_id": "evolveResult",
        "requires_api_key": True,
        "prepare_js": """(() => {
          if (typeof window.clearGroundTruthDraftForm === 'function') {
            try { window.clearGroundTruthDraftForm(); } catch (_) {}
          }
          ['gtJ1', 'gtJ2', 'gtJ3', 'gtJ4', 'gtJ5', 'gtJ6', 'gtJ7', 'gtFinal'].forEach((id) => {
            const input = document.getElementById(id);
            if (!input) return;
            input.value = '';
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
          });
          const submissionSelect = document.getElementById('groundTruthSubmissionSelect');
          if (submissionSelect) {
            submissionSelect.value = '';
            submissionSelect.dispatchEvent(new Event('input', { bubbles: true }));
            submissionSelect.dispatchEvent(new Event('change', { bubbles: true }));
          }
          const result = document.getElementById('evolveResult');
          if (result) result.textContent = '';
          return window.__codexRevealForSmoke('#btnEvolve');
        })()""",
        "expected_text": "学习完成（基于",
    },
    {
        "id": "btnAdaptiveApply",
        "label": "应用补丁（E2E写入，可回滚）",
        "selector": "#btnAdaptiveApply",
        "kind": "result",
        "result_id": "adaptiveApplyResult",
        "requires_api_key": True,
        "prepare_js": """(() => {
          window.__codexRevealForSmoke('#btnAdaptiveApply');
          const result = document.getElementById('adaptiveApplyResult');
          if (result) result.textContent = '';
          return true;
        })()""",
        "timeout_ms": 60000,
        "expected_text": "已应用。变更:",
        "rollback_key": "adaptive_apply",
    },
    {
        "id": "btnCreateProject",
        "label": "创建项目（UI写入）",
        "selector": "#btnCreateProject",
        "kind": "js_check",
        "requires_api_key": True,
        "prepare_js": """(() => {
          const output = document.getElementById('output');
          if (output) output.textContent = '';
          const msg = document.getElementById('createProjectMessage');
          if (msg) msg.textContent = '';
          return window.__codexPrepareUiProjectCreateForSmoke('E2E_UI_');
        })()""",
        "verify_js": """async () => {
          const state = window.__codexSmokeState || {};
          const expectedName = String(state.uiCreatedProjectName || '').trim();
          const output = document.getElementById('output');
          let payload = {};
          try { payload = JSON.parse(String((output && output.textContent) || '{}')); } catch (_) {}
          let createdId = String(
            (payload && payload.id)
            || state.uiCreatedProjectId
            || localStorage.getItem('selected_project_id')
            || ''
          ).trim();
          const createdName = String((payload && payload.name) || '').trim();
          const message = String(((document.getElementById('createProjectMessage') || {}).textContent) || '').trim();
          if (!createdId && expectedName) {
            try {
              const res = await fetch('/api/v1/projects?t=' + Date.now(), { cache: 'no-store' });
              const rows = await res.json().catch(() => ([]));
              const matchedRow = Array.isArray(rows)
                ? rows.find((item) => String((item && item.name) || '').trim() === expectedName)
                : null;
              if (matchedRow && matchedRow.id) {
                createdId = String(matchedRow.id || '').trim();
              }
            } catch (_) {}
          }
          const select = document.getElementById('projectSelect');
          const options = Array.from((select && select.options) || []);
          const matchedOption = options.find((item) => {
            const optionId = String(item.value || '').trim();
            const optionLabel = String(item.textContent || '').trim();
            if (createdId && optionId === createdId) return true;
            return !!expectedName && optionLabel.includes(expectedName);
          });
          if (!createdId && matchedOption) createdId = String(matchedOption.value || '').trim();
          if (createdId) {
            state.uiCreatedProjectId = createdId;
            try { localStorage.setItem('selected_project_id', createdId); } catch (_) {}
            if (select && matchedOption) select.value = createdId;
          }
          const matched = !!createdId && (
            (!!expectedName && String(createdName || message || '').includes(expectedName))
            || !!matchedOption
          );
          return matched;
        }""",
        "observed_js": """() => {
          const msg = document.getElementById('createProjectMessage');
          return String((msg && msg.textContent) || '');
        }""",
        "timeout_ms": 60000,
    },
    {
        "id": "btnCreateProjectFromTender",
        "label": "招标文件自动建项（UI写入）",
        "selector": "#btnCreateProjectFromTender",
        "kind": "js_check",
        "requires_api_key": True,
        "file_input_selector": "#createProjectFromTenderFile",
        "input_files": [str(ROOT_DIR / "tests" / "fixtures" / "browser_smoke_tender_qa.txt")],
        "prepare_js": """(() => window.__codexPrepareUiProjectCreateFromTenderForSmoke('E2E_TENDER_'))()""",
        "verify_js": """async () => {
          const state = window.__codexSmokeState || {};
          const expectedName = String(state.uiTenderCreatedProjectName || '').trim();
          const output = document.getElementById('output');
          let payload = {};
          try { payload = JSON.parse(String((output && output.textContent) || '{}')); } catch (_) {}
          const projectRow = payload && payload.project && typeof payload.project === 'object'
            ? payload.project
            : {};
          let createdId = String(
            projectRow.id
            || state.uiTenderCreatedProjectId
            || localStorage.getItem('selected_project_id')
            || ''
          ).trim();
          const createdName = String(projectRow.name || '').trim();
          const message = String(((document.getElementById('createProjectMessage') || {}).textContent) || '').trim();
          if (!createdId && expectedName) {
            try {
              const res = await fetch('/api/v1/projects?t=' + Date.now(), { cache: 'no-store' });
              const rows = await res.json().catch(() => ([]));
              const matchedRow = Array.isArray(rows)
                ? rows.find((item) => String((item && item.name) || '').trim() === expectedName)
                : null;
              if (matchedRow && matchedRow.id) {
                createdId = String(matchedRow.id || '').trim();
              }
            } catch (_) {}
          }
          if (createdId) {
            state.uiTenderCreatedProjectId = createdId;
            try { localStorage.setItem('selected_project_id', createdId); } catch (_) {}
          }
          return !!createdId && (
            (!!expectedName && String(createdName || message || '').includes(expectedName))
            || String(message || '').includes('创建成功后的下一步')
            || String(message || '').includes('系统当前建议')
          );
        }""",
        "observed_js": """() => {
          const message = document.getElementById('createProjectMessage');
          return String((message && message.textContent) || '');
        }""",
        "timeout_ms": 60000,
    },
    {
        "id": "deleteCurrentProject",
        "label": "删除当前项目（UI写入）",
        "selector": "#deleteCurrentProject",
        "kind": "js_check",
        "requires_api_key": True,
        "prepare_js": """(() => {
          const state = window.__codexSmokeState || {};
          const createdId = String(state.uiCreatedProjectId || '').trim();
          const createdName = String(state.uiCreatedProjectName || createdId || '').trim();
          const select = document.getElementById('projectSelect');
          if (select && createdId) {
            let option = Array.from(select.options || []).find(
              (item) => String(item.value || '').trim() === createdId
            );
            if (!option) {
              option = document.createElement('option');
              option.value = createdId;
              option.textContent = createdName || createdId;
              option.dataset.projectName = createdName || createdId;
              select.appendChild(option);
            }
            select.value = createdId;
          }
          try {
            if (createdId) localStorage.setItem('selected_project_id', createdId);
          } catch (_) {}
          const currentId = String((select && select.value) || createdId || '').trim();
          state.uiDeleteTargetId = currentId;
          const output = document.getElementById('output');
          if (output) output.textContent = '';
          if (typeof window.updateProjectBoundControlsState === 'function') {
            try { window.updateProjectBoundControlsState(); } catch (_) {}
          }
          return !!currentId && window.__codexRevealForSmoke('#deleteCurrentProject');
        })()""",
        "precondition_js": """(() => {
          const state = window.__codexSmokeState || {};
          const createdId = String(state.uiCreatedProjectId || '').trim();
          const currentId = String(((document.getElementById('projectSelect') || {}).value) || '').trim();
          return !!createdId && createdId === currentId;
        })()""",
        "api_verify": "state_project_removed",
        "api_verify_state_key": "uiDeleteTargetId",
        "api_verify_timeout_ms": 30000,
        "observed_js": """() => {
          const output = document.getElementById('output');
          return String((output && output.textContent) || '');
        }""",
    },
    {
        "id": "btnCleanupE2EProjects",
        "label": "清理 E2E 项目（E2E写入，末尾）",
        "selector": "#btnCleanupE2EProjects",
        "kind": "js_check",
        "requires_api_key": True,
        "prepare_js": """() => window.__codexRevealForSmoke('#btnCleanupE2EProjects')""",
        "api_verify": "project_removed",
        "api_verify_timeout_ms": 30000,
        "observed_js": """() => {
          const output = document.getElementById('output');
          return String((output && output.textContent) || '');
        }""",
    },
    {
        "id": "deleteSelectedProjects",
        "label": "删除所选项目（受控批量删除）",
        "selector": "#deleteSelectedProjects",
        "kind": "js_check",
        "requires_api_key": True,
        "prepare_js": """(async () => {
          window.__codexRevealForSmoke('#deleteSelectedProjects');
          const output = document.getElementById('output');
          if (output) output.textContent = '';
          const status = document.getElementById('selectProjectMessage');
          if (status) status.textContent = '';
          return window.__codexPrepareBatchDeleteProjectsForSmoke('E2E_BULK_DELETE_');
        })()""",
        "api_verify": "state_projects_removed",
        "api_verify_state_key": "batchDeleteProjectIds",
        "api_verify_timeout_ms": 30000,
        "observed_js": """() => {
          const output = document.getElementById('output');
          const status = document.getElementById('selectProjectMessage');
          const state = window.__codexSmokeState || {};
          return JSON.stringify({
            output: String((output && output.textContent) || ''),
            status: String((status && status.textContent) || ''),
            ids: Array.isArray(state.batchDeleteProjectIds) ? state.batchDeleteProjectIds : [],
          });
        }""",
        "rollback_key": "batch_delete_projects",
    },
]


def summarize_text(value: str, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def resolve_project_context(project_id: str, summary_file: Path) -> Dict[str, str]:
    explicit = str(project_id or "").strip()
    context = {"project_id": explicit, "project_name": ""}
    if explicit or not summary_file.exists():
        return context
    try:
        data = json.loads(summary_file.read_text(encoding="utf-8"))
    except Exception:
        return context
    return {
        "project_id": str(data.get("project_id") or "").strip(),
        "project_name": str(data.get("project_name") or "").strip(),
    }


def resolve_project_id(project_id: str, summary_file: Path) -> str:
    return resolve_project_context(project_id, summary_file)["project_id"]


def write_smoke_enabled(project_name: str) -> bool:
    override = str(os.environ.get("PLAYWRIGHT_SMOKE_ALLOW_WRITE", "") or "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    return str(project_name or "").strip().lower().startswith("e2e_")


def resolve_admin_api_key() -> str:
    resolver = ROOT_DIR / "scripts" / "resolve_api_key.py"
    if not resolver.exists():
        return ""
    try:
        result = subprocess.run(
            [sys.executable, str(resolver), "--preferred-role", "admin"],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return str(result.stdout or "").strip()


def capture_adaptive_apply_snapshot() -> Dict[str, Any]:
    resources_dir = LEXICON_PATH.parent
    existing_backups = {
        str(path.resolve())
        for pattern in ("lexicon.yaml.bak_*", "rubric.yaml.bak_*")
        for path in resources_dir.glob(pattern)
    }
    return {
        "lexicon_text": LEXICON_PATH.read_text(encoding="utf-8"),
        "rubric_text": RUBRIC_PATH.read_text(encoding="utf-8"),
        "existing_backups": existing_backups,
    }


def restore_adaptive_apply_snapshot(
    snapshot: Dict[str, Any], *, base_url: str, api_key: str
) -> None:
    if not snapshot:
        return
    LEXICON_PATH.write_text(str(snapshot.get("lexicon_text") or ""), encoding="utf-8")
    RUBRIC_PATH.write_text(str(snapshot.get("rubric_text") or ""), encoding="utf-8")
    existing_backups = {str(item) for item in (snapshot.get("existing_backups") or set())}
    for pattern in ("lexicon.yaml.bak_*", "rubric.yaml.bak_*"):
        for candidate in LEXICON_PATH.parent.glob(pattern):
            resolved = str(candidate.resolve())
            if resolved in existing_backups:
                continue
            try:
                candidate.unlink()
            except FileNotFoundError:
                continue
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    request = Request(
        f"{base_url.rstrip('/')}/api/v1/config/reload",
        method="POST",
        headers=headers,
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:  # pragma: no cover - runtime-only branch
        raise RuntimeError(f"config reload failed: HTTP {exc.code}") from exc
    except URLError as exc:  # pragma: no cover - runtime-only branch
        raise RuntimeError(f"config reload failed: {exc.reason}") from exc
    except Exception as exc:  # pragma: no cover - runtime-only branch
        raise RuntimeError(f"config reload failed: {exc}") from exc
    if not bool(payload.get("reloaded")):
        raise RuntimeError(f"config reload failed: {payload.get('message') or 'unknown'}")


def project_exists_via_api(*, base_url: str, project_id: str, api_key: str) -> bool:
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    request = Request(f"{base_url.rstrip('/')}/api/v1/projects", headers=headers)
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8") or "[]")
    return any(str(item.get("id") or "").strip() == project_id for item in (payload or []))


def wait_for_projects_removed(
    *,
    base_url: str,
    project_ids: List[str],
    api_key: str,
    timeout_ms: int,
) -> bool:
    ids = [str(item or "").strip() for item in project_ids if str(item or "").strip()]
    if not ids:
        return True
    deadline = time.time() + max(1.0, float(timeout_ms) / 1000.0)
    while time.time() < deadline:
        try:
            remaining = [
                project_id
                for project_id in ids
                if project_exists_via_api(base_url=base_url, project_id=project_id, api_key=api_key)
            ]
            if not remaining:
                return True
        except Exception:
            pass
        time.sleep(0.75)
    return False


def wait_for_project_removed(
    *, base_url: str, project_id: str, api_key: str, timeout_ms: int
) -> bool:
    deadline = time.time() + max(1.0, float(timeout_ms) / 1000.0)
    while time.time() < deadline:
        try:
            if not project_exists_via_api(
                base_url=base_url, project_id=project_id, api_key=api_key
            ):
                return True
        except Exception:
            pass
        time.sleep(0.75)
    return False


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture_config_integrity_snapshot() -> Dict[str, Any]:
    resources_dir = LEXICON_PATH.parent
    backups = sorted(
        str(path.resolve())
        for pattern in ("lexicon.yaml.bak_*", "rubric.yaml.bak_*")
        for path in resources_dir.glob(pattern)
    )
    return {
        "files": {
            str(LEXICON_PATH.resolve()): file_sha256(LEXICON_PATH),
            str(RUBRIC_PATH.resolve()): file_sha256(RUBRIC_PATH),
        },
        "backups": backups,
    }


def verify_config_integrity_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    current_files = {
        str(LEXICON_PATH.resolve()): file_sha256(LEXICON_PATH),
        str(RUBRIC_PATH.resolve()): file_sha256(RUBRIC_PATH),
    }
    current_backups = sorted(
        str(path.resolve())
        for pattern in ("lexicon.yaml.bak_*", "rubric.yaml.bak_*")
        for path in LEXICON_PATH.parent.glob(pattern)
    )
    expected_files = {
        str(key): str(value) for key, value in dict(snapshot.get("files") or {}).items()
    }
    expected_backups = sorted(str(item) for item in (snapshot.get("backups") or []))
    return {
        "ok": current_files == expected_files and current_backups == expected_backups,
        "files_ok": current_files == expected_files,
        "backups_ok": current_backups == expected_backups,
        "current_files": current_files,
        "expected_files": expected_files,
        "current_backups": current_backups,
        "expected_backups": expected_backups,
    }


def build_step_matrix(include_write: bool = False) -> List[Dict[str, Any]]:
    matrix = list(BUTTON_SMOKE_MATRIX)
    if include_write:
        matrix.extend(WRITE_BUTTON_SMOKE_MATRIX)
    return matrix


def build_report_markdown(report: Dict[str, Any]) -> str:
    overall = "SKIP" if report.get("skipped") else ("PASS" if report.get("ok") else "FAIL")
    lines = [
        "# Browser Button Smoke",
        "",
        f"- Overall: `{overall}`",
        f"- Base URL: `{report.get('base_url', '')}`",
        f"- Project ID: `{report.get('project_id', '')}`",
        f"- Project Name: `{report.get('project_name', '')}`",
        f"- Write Smoke Enabled: `{report.get('write_smoke_enabled', False)}`",
        f"- Screenshot: `{report.get('screenshot_path', '')}`",
        f"- Console log: `{report.get('console_log_path', '')}`",
        "",
        "## Step Results",
        "",
    ]
    for step in report.get("steps") or []:
        status = "PASS" if step.get("ok") else ("SKIP" if step.get("skipped") else "FAIL")
        lines.append(f"### {step.get('label', step.get('id', 'step'))}")
        lines.append(f"- Status: `{status}`")
        if step.get("selector"):
            lines.append(f"- Selector: `{step['selector']}`")
        if step.get("result_id"):
            lines.append(f"- Result block: `{step['result_id']}`")
        if step.get("downloaded_filename"):
            lines.append(f"- Download: `{step['downloaded_filename']}`")
        if step.get("saved_path"):
            lines.append(f"- Saved path: `{step['saved_path']}`")
        if step.get("observed_text"):
            lines.append(f"- Observed: `{step['observed_text']}`")
        if step.get("reason"):
            lines.append(f"- Reason: `{step['reason']}`")
        if step.get("error"):
            lines.append(f"- Error: `{step['error']}`")
        lines.append("")
    page_errors = report.get("page_errors") or []
    if page_errors:
        lines.extend(["## Page Errors", ""])
        for item in page_errors:
            lines.append(f"- `{item}`")
        lines.append("")
    console_errors = report.get("console_errors") or []
    if console_errors:
        lines.extend(["## Console Errors", ""])
        for item in console_errors:
            lines.append(f"- `{item}`")
        lines.append("")
    config_integrity = report.get("config_integrity") or {}
    if config_integrity:
        status = "PASS" if config_integrity.get("ok") else "FAIL"
        lines.extend(["## Config Integrity", ""])
        lines.append(f"- Status: `{status}`")
        lines.append(f"- Files OK: `{config_integrity.get('files_ok', False)}`")
        lines.append(f"- Backups OK: `{config_integrity.get('backups_ok', False)}`")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _import_playwright():
    try:
        from playwright.sync_api import Error as PlaywrightError  # type: ignore
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # type: ignore
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime-only branch
        return None, None, None, str(exc)
    return sync_playwright, PlaywrightTimeoutError, PlaywrightError, ""


def browser_launch_candidates(requested_channel: str) -> List[Dict[str, str]]:
    candidates: List[Dict[str, str]] = []
    requested = str(requested_channel or "").strip()
    if requested:
        candidates.append({"channel": requested, "executable_path": "", "label": requested})
    if not requested:
        candidates.append({"channel": "", "executable_path": "", "label": "chromium-default"})
    if Path(SYSTEM_CHROME_EXECUTABLE).exists():
        candidates.append(
            {
                "channel": "",
                "executable_path": SYSTEM_CHROME_EXECUTABLE,
                "label": "chrome-executable",
            }
        )
    if requested or not candidates:
        candidates.append({"channel": "", "executable_path": "", "label": "chromium-default"})
    return candidates


def run_browser_smoke(
    *,
    base_url: str,
    project_id: str,
    project_name: str,
    artifact_dir: Path,
    timeout_ms: int,
) -> Tuple[Dict[str, Any], int]:
    sync_playwright, PlaywrightTimeoutError, PlaywrightError, import_error = _import_playwright()
    report: Dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "base_url": base_url,
        "project_id": project_id,
        "project_name": project_name,
        "artifact_dir": str(artifact_dir),
        "steps": [],
        "page_errors": [],
        "console_errors": [],
        "screenshot_path": str(artifact_dir / "final.png"),
        "html_path": str(artifact_dir / "final.html"),
        "console_log_path": str(artifact_dir / "console.log"),
    }
    if sync_playwright is None:
        report["skipped"] = True
        report["reason"] = f"playwright unavailable: {import_error}"
        return report, 2

    artifact_dir.mkdir(parents=True, exist_ok=True)
    download_dir = artifact_dir / ".downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    console_lines: List[str] = []
    config_integrity_snapshot = capture_config_integrity_snapshot()
    write_enabled = write_smoke_enabled(project_name)
    admin_api_key = resolve_admin_api_key() if write_enabled else ""
    effective_write_enabled = write_enabled and bool(admin_api_key)
    report["write_smoke_enabled"] = effective_write_enabled
    if write_enabled and not admin_api_key:
        report["write_smoke_reason"] = "admin api key unavailable"

    browser = None
    context = None
    page = None

    def append_step(item: Dict[str, Any]) -> None:
        report["steps"].append(item)

    def launch_browser(playwright: Any) -> Any:
        requested_channel = str(os.environ.get("PLAYWRIGHT_BROWSER_CHANNEL", "") or "").strip()
        retry_count = max(1, int(os.environ.get("PLAYWRIGHT_LAUNCH_RETRIES", "3") or "3"))
        launch_attempts = []
        candidates = browser_launch_candidates(requested_channel)
        last_error = None
        for candidate in candidates:
            for attempt in range(1, retry_count + 1):
                launch_kwargs = {
                    "headless": os.environ.get("PLAYWRIGHT_HEADLESS", "1") != "0",
                }
                if candidate["channel"]:
                    launch_kwargs["channel"] = candidate["channel"]
                if candidate.get("executable_path"):
                    launch_kwargs["executable_path"] = candidate["executable_path"]
                try:
                    launched = playwright.chromium.launch(**launch_kwargs)
                    report["browser_launch_mode"] = candidate["label"]
                    report["browser_launch_attempt"] = attempt
                    if launch_attempts:
                        report["browser_launch_fallbacks"] = launch_attempts
                    return launched
                except Exception as exc:  # pragma: no cover - runtime-only branch
                    last_error = exc
                    launch_attempts.append(
                        {
                            "mode": candidate["label"],
                            "attempt": attempt,
                            "error": str(exc),
                        }
                    )
                    if attempt < retry_count:
                        time.sleep(min(3.0, float(attempt)))
        report["browser_launch_fallbacks"] = launch_attempts
        if last_error:
            raise last_error
        raise RuntimeError("no browser launch candidates available")

    def run_page_script(source: str) -> Any:
        script = str(source or "").strip()
        if not script:
            return None
        result = page.evaluate(script)
        page.wait_for_timeout(120)
        return result

    def prime_step(spec: Dict[str, Any]) -> None:
        page.evaluate(
            """async ({ projectId, projectName }) => {
              if (typeof window.__codexForceProjectContextForSmoke === 'function') {
                return window.__codexForceProjectContextForSmoke(projectId, projectName);
              }
              return false;
            }""",
            {"projectId": project_id, "projectName": project_name},
        )
        if admin_api_key:
            page.evaluate(
                """(value) => {
                  window.__codexSmokeState = window.__codexSmokeState || {};
                  window.__codexSmokeState.adminApiKey = String(value || '');
                }""",
                admin_api_key,
            )
        if spec.get("requires_api_key") and admin_api_key:
            page.evaluate(
                """(value) => {
                  if (typeof window.__codexSetApiKeyForSmoke === 'function') {
                    return window.__codexSetApiKeyForSmoke(value);
                  }
                  return false;
                }""",
                admin_api_key,
            )
        run_page_script(str(spec.get("prepare_js") or ""))
        run_page_script(str(spec.get("prime_js") or ""))
        prime_wait_selector = str(spec.get("prime_wait_selector") or "").strip()
        if prime_wait_selector:
            page.wait_for_selector(prime_wait_selector, timeout=timeout_ms)

    def install_input_files(spec: Dict[str, Any]) -> None:
        input_selector = str(spec.get("file_input_selector") or "").strip()
        input_files = spec.get("input_files") or []
        if not input_selector or not input_files:
            return
        normalized_files = [str(Path(item).resolve()) for item in input_files]
        locator = page.locator(input_selector).first
        if locator.count() < 1:
            raise RuntimeError(f"file input not present: {input_selector}")
        locator.set_input_files(normalized_files)

    def capture_step_rollback(spec: Dict[str, Any]) -> Dict[str, Any] | None:
        rollback_key = str(spec.get("rollback_key") or "").strip()
        if rollback_key == "adaptive_apply":
            return capture_adaptive_apply_snapshot()
        if rollback_key == "batch_delete_projects":
            return {}
        return None

    def apply_step_rollback(spec: Dict[str, Any], rollback_state: Dict[str, Any] | None) -> str:
        rollback_key = str(spec.get("rollback_key") or "").strip()
        if not rollback_key:
            return ""
        if rollback_key == "adaptive_apply":
            try:
                restore_adaptive_apply_snapshot(
                    rollback_state or {},
                    base_url=base_url,
                    api_key=admin_api_key,
                )
            except Exception as exc:  # pragma: no cover - runtime-only branch
                return str(exc)
        if rollback_key == "batch_delete_projects":
            try:
                project_ids = page.evaluate(
                    """() => {
                      const state = window.__codexSmokeState || {};
                      return Array.isArray(state.batchDeleteProjectIds)
                        ? state.batchDeleteProjectIds.map((item) => String(item || ''))
                        : [];
                    }"""
                )
            except Exception:
                project_ids = []
            if isinstance(project_ids, list):
                for project_id in project_ids:
                    normalized = str(project_id or "").strip()
                    if not normalized:
                        continue
                    headers = {}
                    if admin_api_key:
                        headers["X-API-Key"] = admin_api_key
                    request = Request(
                        f"{base_url.rstrip('/')}/api/v1/projects/{normalized}",
                        method="DELETE",
                        headers=headers,
                    )
                    try:
                        with urlopen(request, timeout=30):
                            pass
                    except HTTPError as exc:
                        if exc.code != 404:
                            return f"batch delete rollback failed: HTTP {exc.code}"
                    except Exception as exc:  # pragma: no cover - runtime-only branch
                        return f"batch delete rollback failed: {exc}"
            try:
                page.evaluate(
                    """() => {
                      const state = window.__codexSmokeState || {};
                      state.batchDeleteProjectIds = [];
                      state.batchDeleteProjectNames = [];
                      return true;
                    }"""
                )
            except Exception:
                pass
        return ""

    def open_locator_ancestors(selector: str) -> None:
        locator = page.locator(selector).first
        if locator.count() < 1:
            return
        locator.evaluate(
            """el => {
              let node = el;
              while (node) {
                if (node.tagName === 'DETAILS') node.open = true;
                node = node.parentElement;
              }
            }"""
        )

    def ensure_clickable(selector: str) -> bool:
        locator = page.locator(selector).first
        if locator.count() < 1:
            return False
        open_locator_ancestors(selector)
        page.wait_for_timeout(100)
        locator.scroll_into_view_if_needed()
        return True

    def reopen_guidance_result(required_selector: str = "") -> None:
        locator = page.locator("#btnWritingGuidance").first
        if locator.count() < 1:
            return
        ensure_clickable("#btnWritingGuidance")
        locator.click()
        if required_selector:
            page.wait_for_selector(required_selector, timeout=min(timeout_ms, 5000))
            return
        page.wait_for_function(
            """() => {
              const el = document.getElementById('guidanceResult');
              if (!el) return false;
              const display = window.getComputedStyle(el).display;
              if (display === 'none') return false;
              return String(el.textContent || '').includes('高分逻辑');
            }""",
            timeout=timeout_ms,
        )

    def result_step(spec: Dict[str, Any]) -> None:
        item: Dict[str, Any] = {
            "id": spec["id"],
            "label": spec["label"],
            "selector": spec["selector"],
            "kind": spec["kind"],
            "result_id": spec["result_id"],
            "ok": False,
            "skipped": False,
        }
        step_timeout_ms = int(spec.get("timeout_ms") or timeout_ms)
        rollback_state: Dict[str, Any] | None = None
        appended = False
        try:
            rollback_state = capture_step_rollback(spec)
            prime_step(spec)
            install_input_files(spec)
            locator = page.locator(spec["selector"]).first
            if locator.count() < 1:
                if spec.get("optional"):
                    item["skipped"] = True
                    item["reason"] = "selector not present"
                    appended = True
                    append_step(item)
                    return
                item["error"] = "selector not present"
                appended = True
                append_step(item)
                return
            ensure_clickable(spec["selector"])
            locator.click()
            page.wait_for_function(
                """([resultId, expectedText, expectedOutputText]) => {
                  const el = document.getElementById(resultId);
                  const outputEl = document.getElementById('output');
                  const outputReady = expectedOutputText
                    ? String((outputEl && outputEl.textContent) || '').includes(expectedOutputText)
                    : false;
                  if (el) {
                    const display = window.getComputedStyle(el).display;
                    if (display !== 'none' && String(el.textContent || '').includes(expectedText)) {
                      return true;
                    }
                  }
                  return outputReady;
                }""",
                arg=[
                    spec["result_id"],
                    spec["expected_text"],
                    str(spec.get("expected_output_text") or ""),
                ],
                timeout=step_timeout_ms,
            )
            verify_js = str(spec.get("verify_js") or "").strip()
            if verify_js:
                page.wait_for_function(verify_js, timeout=step_timeout_ms)
            observed = page.locator(f"#{spec['result_id']}").text_content() or ""
            item["ok"] = True
            item["observed_text"] = summarize_text(observed)
        except PlaywrightTimeoutError as exc:
            item["error"] = f"timeout: {exc}"
        except PlaywrightError as exc:
            item["error"] = str(exc)
        finally:
            rollback_error = apply_step_rollback(spec, rollback_state)
            if rollback_error:
                item["ok"] = False
                if item.get("error"):
                    item["error"] = f"{item['error']} | rollback: {rollback_error}"
                else:
                    item["error"] = f"rollback: {rollback_error}"
            if not appended:
                append_step(item)

    def download_step(spec: Dict[str, Any]) -> None:
        item: Dict[str, Any] = {
            "id": spec["id"],
            "label": spec["label"],
            "selector": spec["selector"],
            "kind": spec["kind"],
            "ok": False,
            "skipped": False,
        }
        try:
            prime_step(spec)
            if spec["id"].startswith("btnGuidancePatchBundleInline"):
                try:
                    reopen_guidance_result(spec["selector"])
                except PlaywrightTimeoutError:
                    pass
            locator = page.locator(spec["selector"]).first
            if locator.count() < 1:
                if spec["id"].startswith("btnGuidancePatchBundleInline"):
                    try:
                        page.wait_for_selector(spec["selector"], timeout=min(timeout_ms, 5000))
                    except PlaywrightTimeoutError:
                        pass
                    locator = page.locator(spec["selector"]).first
            if locator.count() < 1:
                if spec.get("optional"):
                    item["skipped"] = True
                    item["reason"] = "selector not present"
                    append_step(item)
                    return
                item["error"] = "selector not present"
                append_step(item)
                return
            ensure_clickable(spec["selector"])
            with page.expect_download(timeout=timeout_ms) as download_info:
                locator.click()
            download = download_info.value
            suggested = download.suggested_filename
            expected_prefix = str(spec.get("expected_filename_prefix") or "")
            expected_suffix = str(spec.get("expected_filename_suffix") or "")
            if expected_prefix and not suggested.startswith(expected_prefix):
                item["error"] = f"unexpected filename: {suggested}"
                append_step(item)
                return
            if expected_suffix and not suggested.endswith(expected_suffix):
                item["error"] = f"unexpected filename: {suggested}"
                append_step(item)
                return
            safe_name = f"{spec['id']}--{suggested}"
            saved_path = download_dir / safe_name
            download.save_as(str(saved_path))
            item["ok"] = True
            item["downloaded_filename"] = suggested
            item["saved_path"] = str(saved_path)
        except PlaywrightTimeoutError as exc:
            item["error"] = f"timeout: {exc}"
        except PlaywrightError as exc:
            item["error"] = str(exc)
        append_step(item)

    def js_check_step(spec: Dict[str, Any]) -> None:
        item: Dict[str, Any] = {
            "id": spec["id"],
            "label": spec["label"],
            "selector": spec["selector"],
            "kind": spec["kind"],
            "ok": False,
            "skipped": False,
        }
        step_timeout_ms = int(spec.get("timeout_ms") or timeout_ms)
        rollback_state: Dict[str, Any] | None = None
        appended = False
        try:
            if spec.get("requires_api_key") and not effective_write_enabled:
                item["skipped"] = True
                item["reason"] = report.get("write_smoke_reason", "write smoke disabled")
                appended = True
                append_step(item)
                return
            rollback_state = capture_step_rollback(spec)
            prime_step(spec)
            install_input_files(spec)
            precondition_js = str(spec.get("precondition_js") or "").strip()
            if precondition_js:
                if page.evaluate(precondition_js):
                    pass
                elif spec.get("precondition_optional"):
                    item["skipped"] = True
                    item["reason"] = "precondition not met"
                    appended = True
                    append_step(item)
                    return
                else:
                    item["error"] = "precondition not met"
                    appended = True
                    append_step(item)
                    return
            locator = page.locator(spec["selector"]).first
            if locator.count() < 1:
                if spec.get("optional"):
                    item["skipped"] = True
                    item["reason"] = "selector not present"
                    appended = True
                    append_step(item)
                    return
                item["error"] = "selector not present"
                appended = True
                append_step(item)
                return
            ensure_clickable(spec["selector"])
            locator.click()
            verify_js = str(spec.get("verify_js") or "").strip()
            if verify_js:
                page.wait_for_function(verify_js, timeout=step_timeout_ms)
            api_verify = str(spec.get("api_verify") or "").strip()
            if api_verify == "project_removed":
                verify_timeout_ms = int(spec.get("api_verify_timeout_ms") or step_timeout_ms)
                if not wait_for_project_removed(
                    base_url=base_url,
                    project_id=project_id,
                    api_key=admin_api_key,
                    timeout_ms=verify_timeout_ms,
                ):
                    raise RuntimeError("project_removed verification failed")
            if api_verify == "state_project_removed":
                verify_timeout_ms = int(spec.get("api_verify_timeout_ms") or step_timeout_ms)
                state_key = str(spec.get("api_verify_state_key") or "").strip()
                if not state_key:
                    raise RuntimeError("state_project_removed missing state key")
                target_project_id = str(
                    page.evaluate(
                        """(key) => {
                          const state = window.__codexSmokeState || {};
                          return String(state[key] || '');
                        }""",
                        state_key,
                    )
                    or ""
                ).strip()
                if not target_project_id:
                    raise RuntimeError("state_project_removed missing target project id")
                if not wait_for_project_removed(
                    base_url=base_url,
                    project_id=target_project_id,
                    api_key=admin_api_key,
                    timeout_ms=verify_timeout_ms,
                ):
                    raise RuntimeError("state_project_removed verification failed")
            if api_verify == "state_projects_removed":
                verify_timeout_ms = int(spec.get("api_verify_timeout_ms") or step_timeout_ms)
                state_key = str(spec.get("api_verify_state_key") or "").strip()
                if not state_key:
                    raise RuntimeError("state_projects_removed missing state key")
                target_project_ids = page.evaluate(
                    """(key) => {
                      const state = window.__codexSmokeState || {};
                      const rows = state[key];
                      return Array.isArray(rows) ? rows.map((item) => String(item || '')) : [];
                    }""",
                    state_key,
                )
                if not isinstance(target_project_ids, list) or not any(
                    str(item or "").strip() for item in target_project_ids
                ):
                    raise RuntimeError("state_projects_removed missing target project ids")
                if not wait_for_projects_removed(
                    base_url=base_url,
                    project_ids=[str(item or "").strip() for item in target_project_ids],
                    api_key=admin_api_key,
                    timeout_ms=verify_timeout_ms,
                ):
                    raise RuntimeError("state_projects_removed verification failed")
            observed_js = str(spec.get("observed_js") or "").strip()
            if observed_js:
                observed = page.evaluate(observed_js)
                item["observed_text"] = summarize_text(str(observed or ""))
            item["ok"] = True
        except PlaywrightTimeoutError as exc:
            item["error"] = f"timeout: {exc}"
        except PlaywrightError as exc:
            item["error"] = str(exc)
        finally:
            rollback_error = apply_step_rollback(spec, rollback_state)
            if rollback_error:
                item["ok"] = False
                if item.get("error"):
                    item["error"] = f"{item['error']} | rollback: {rollback_error}"
                else:
                    item["error"] = f"rollback: {rollback_error}"
            if not appended:
                append_step(item)

    try:
        with sync_playwright() as p:
            browser = launch_browser(p)
            context = browser.new_context(
                accept_downloads=True, viewport={"width": 1600, "height": 1200}
            )
            page = context.new_page()
            page.on(
                "console",
                lambda msg: console_lines.append(f"{msg.type.upper()}: {msg.text}"),
            )
            page.on("pageerror", lambda exc: report["page_errors"].append(str(exc)))
            page.goto(
                f"{base_url.rstrip('/')}/?project_id={project_id}",
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            page.wait_for_timeout(1500)
            page.wait_for_selector("#projectSelect", timeout=timeout_ms)
            page.evaluate(SMOKE_PAGE_HELPERS)
            if admin_api_key:
                page.evaluate(
                    """(value) => {
                      if (typeof window.__codexSetApiKeyForSmoke === 'function') {
                        window.__codexSetApiKeyForSmoke(value);
                      }
                    }""",
                    admin_api_key,
                )
            for spec in build_step_matrix(effective_write_enabled):
                if spec["kind"] == "result":
                    result_step(spec)
                elif spec["kind"] == "js_check":
                    js_check_step(spec)
                else:
                    download_step(spec)
            page.screenshot(path=str(artifact_dir / "final.png"), full_page=True)
            (artifact_dir / "final.html").write_text(page.content(), encoding="utf-8")
    except PlaywrightError as exc:  # pragma: no cover - runtime-only branch
        report["fatal_error"] = str(exc)
    except Exception as exc:  # pragma: no cover - runtime-only branch
        report["fatal_error"] = str(exc)
    finally:
        try:
            if page is not None:
                page.close()
        except Exception:
            pass
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        (artifact_dir / "console.log").write_text("\n".join(console_lines), encoding="utf-8")

    report["console_errors"] = [
        line for line in console_lines if line.startswith("ERROR:") or line.startswith("PAGEERROR:")
    ]
    report["config_integrity"] = verify_config_integrity_snapshot(config_integrity_snapshot)
    report["ok"] = bool(
        not report.get("fatal_error")
        and not report["page_errors"]
        and bool((report.get("config_integrity") or {}).get("ok"))
        and all(step.get("ok") or step.get("skipped") for step in report["steps"])
    )
    return report, 0 if report["ok"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Browser smoke for high-value web buttons.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--project-id", default="")
    parser.add_argument("--summary-file", default=str(DEFAULT_SUMMARY_FILE))
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary_file = Path(args.summary_file)
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    artifact_dir = Path(args.artifact_dir)
    context = resolve_project_context(args.project_id, summary_file)
    project_id = context["project_id"]
    project_name = context["project_name"]
    if not project_id:
        report = {
            "ok": False,
            "skipped": True,
            "reason": f"project_id unavailable (summary={summary_file})",
            "steps": [],
        }
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(build_report_markdown(report), encoding="utf-8")
        return 1 if args.strict else 2

    report, rc = run_browser_smoke(
        base_url=str(args.base_url),
        project_id=project_id,
        project_name=project_name,
        artifact_dir=artifact_dir,
        timeout_ms=int(args.timeout_ms),
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(build_report_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
