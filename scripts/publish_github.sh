#!/usr/bin/env bash
set -euo pipefail

repo_name="${1:-$(basename "$(pwd)")}"
visibility="${GITHUB_VISIBILITY:-private}"  # private|public
base_branch="${GITHUB_BASE_BRANCH:-main}"

if ! command -v gh >/dev/null 2>&1; then
  echo "[ERROR] gh 未安装，请先安装 GitHub CLI。" >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "[ERROR] gh 未登录。请先执行：gh auth login" >&2
  exit 1
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "[ERROR] 当前目录不是 git 仓库。" >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[ERROR] 工作区不干净，请先提交或暂存后再发布。" >&2
  exit 1
fi

current_branch="$(git branch --show-current)"
if [[ -z "${current_branch}" ]]; then
  echo "[ERROR] 无法识别当前分支。" >&2
  exit 1
fi

if ! git show-ref --verify --quiet "refs/heads/${base_branch}"; then
  git branch "${base_branch}" "${current_branch}"
fi

git checkout "${base_branch}" >/dev/null

if ! git remote get-url origin >/dev/null 2>&1; then
  case "${visibility}" in
    private)
      gh repo create "${repo_name}" --private --source=. --remote=origin --push
      ;;
    public)
      gh repo create "${repo_name}" --public --source=. --remote=origin --push
      ;;
    *)
      echo "[ERROR] GITHUB_VISIBILITY 仅支持 private/public，当前：${visibility}" >&2
      exit 1
      ;;
  esac
else
  git push -u origin "${base_branch}"
fi

repo_url="$(gh repo view --json url -q .url 2>/dev/null || true)"
if [[ -n "${repo_url}" ]]; then
  echo "[OK] GitHub 仓库已发布：${repo_url}"
else
  echo "[OK] 发布完成。"
fi
