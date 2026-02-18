"""
多语言支持模块 (Internationalization Module)

提供评分报告的多语言翻译功能，支持中文(zh)和英文(en)。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

# 默认语言
DEFAULT_LOCALE = "zh"

# 支持的语言列表
SUPPORTED_LOCALES = ["zh", "en"]

# 翻译文件目录
LOCALES_DIR = Path(__file__).parent / "resources" / "locales"


class I18n:
    """多语言翻译类"""

    def __init__(self, locale: str = DEFAULT_LOCALE):
        """
        初始化翻译实例

        Args:
            locale: 语言代码 (zh/en)
        """
        self._locale = locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE
        self._translations: Dict[str, Any] = {}
        self._load_translations()

    @property
    def locale(self) -> str:
        """当前语言"""
        return self._locale

    @locale.setter
    def locale(self, value: str) -> None:
        """设置语言"""
        if value in SUPPORTED_LOCALES:
            self._locale = value
            self._load_translations()

    def _load_translations(self) -> None:
        """加载翻译文件"""
        locale_file = LOCALES_DIR / f"{self._locale}.yaml"
        if locale_file.exists():
            with open(locale_file, encoding="utf-8") as f:
                self._translations = yaml.safe_load(f) or {}
        else:
            self._translations = {}

    def t(self, key: str, **kwargs: Any) -> str:
        """
        获取翻译文本

        Args:
            key: 翻译键，使用点号分隔层级 (如 "report.title")
            **kwargs: 格式化参数

        Returns:
            翻译后的文本，如果未找到则返回原始键
        """
        parts = key.split(".")
        value: Any = self._translations

        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return key

        if value is None:
            return key

        result = str(value)

        # 支持简单的变量替换
        if kwargs:
            for k, v in kwargs.items():
                result = result.replace(f"{{{k}}}", str(v))

        return result

    def get_all(self, section: str) -> Dict[str, Any]:
        """
        获取整个翻译节

        Args:
            section: 节名称 (如 "report", "scoring")

        Returns:
            该节的所有翻译
        """
        return self._translations.get(section, {})


# 全局翻译实例
_global_i18n: Optional[I18n] = None


def get_i18n(locale: Optional[str] = None) -> I18n:
    """
    获取翻译实例

    Args:
        locale: 语言代码，如果为 None 则使用全局实例

    Returns:
        I18n 实例
    """
    global _global_i18n

    if locale is not None:
        return I18n(locale)

    if _global_i18n is None:
        # 从环境变量或默认值初始化
        env_locale = os.environ.get("QINGTIAN_LOCALE", DEFAULT_LOCALE)
        _global_i18n = I18n(env_locale)

    return _global_i18n


def set_locale(locale: str) -> None:
    """
    设置全局语言

    Args:
        locale: 语言代码 (zh/en)
    """
    global _global_i18n
    if _global_i18n is None:
        _global_i18n = I18n(locale)
    else:
        _global_i18n.locale = locale


def t(key: str, locale: Optional[str] = None, **kwargs: Any) -> str:
    """
    翻译快捷函数

    Args:
        key: 翻译键
        locale: 可选的语言代码，覆盖全局设置
        **kwargs: 格式化参数

    Returns:
        翻译后的文本
    """
    i18n = get_i18n(locale)
    return i18n.t(key, **kwargs)


@lru_cache(maxsize=2)
def load_locale_data(locale: str) -> Dict[str, Any]:
    """
    加载并缓存语言数据

    Args:
        locale: 语言代码

    Returns:
        翻译数据字典
    """
    if locale not in SUPPORTED_LOCALES:
        locale = DEFAULT_LOCALE

    locale_file = LOCALES_DIR / f"{locale}.yaml"
    if locale_file.exists():
        with open(locale_file, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_supported_locales() -> list[str]:
    """获取支持的语言列表"""
    return SUPPORTED_LOCALES.copy()


def is_supported_locale(locale: str) -> bool:
    """检查语言是否支持"""
    return locale in SUPPORTED_LOCALES
