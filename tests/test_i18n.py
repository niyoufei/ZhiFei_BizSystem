"""
测试 i18n 多语言模块
"""

import os
from unittest.mock import patch

from app.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    I18n,
    get_i18n,
    get_supported_locales,
    is_supported_locale,
    load_locale_data,
    set_locale,
    t,
)


class TestI18nClass:
    """测试 I18n 类"""

    def test_init_default_locale(self):
        """测试默认语言初始化"""
        i18n = I18n()
        assert i18n.locale == DEFAULT_LOCALE

    def test_init_with_zh_locale(self):
        """测试中文语言初始化"""
        i18n = I18n("zh")
        assert i18n.locale == "zh"

    def test_init_with_en_locale(self):
        """测试英文语言初始化"""
        i18n = I18n("en")
        assert i18n.locale == "en"

    def test_init_invalid_locale_falls_back(self):
        """测试无效语言回退到默认"""
        i18n = I18n("invalid")
        assert i18n.locale == DEFAULT_LOCALE

    def test_locale_setter_valid(self):
        """测试设置有效语言"""
        i18n = I18n("zh")
        i18n.locale = "en"
        assert i18n.locale == "en"

    def test_locale_setter_invalid(self):
        """测试设置无效语言不改变"""
        i18n = I18n("zh")
        i18n.locale = "invalid"
        assert i18n.locale == "zh"


class TestTranslation:
    """测试翻译功能"""

    def test_translate_zh_report_title(self):
        """测试中文报告标题"""
        i18n = I18n("zh")
        title = i18n.t("report.title")
        assert "青天" in title

    def test_translate_en_report_title(self):
        """测试英文报告标题"""
        i18n = I18n("en")
        title = i18n.t("report.title")
        assert "Qingtian" in title

    def test_translate_zh_scoring(self):
        """测试中文评分翻译"""
        i18n = I18n("zh")
        total = i18n.t("scoring.total_score")
        assert "总分" in total

    def test_translate_en_scoring(self):
        """测试英文评分翻译"""
        i18n = I18n("en")
        total = i18n.t("scoring.total_score")
        assert "Total Score" in total

    def test_translate_missing_key(self):
        """测试缺失键返回原始键"""
        i18n = I18n("zh")
        result = i18n.t("nonexistent.key")
        assert result == "nonexistent.key"

    def test_translate_partial_key(self):
        """测试部分键"""
        i18n = I18n("zh")
        result = i18n.t("report.nonexistent")
        assert result == "report.nonexistent"

    def test_translate_with_kwargs(self):
        """测试带参数翻译（占位符替换）"""
        i18n = I18n("zh")
        # 翻译文件中没有占位符，但功能应该工作
        result = i18n.t("scoring.total_score", value="100")
        assert "总分" in result


class TestGetAll:
    """测试获取整个翻译节"""

    def test_get_all_report_section_zh(self):
        """测试获取中文报告节"""
        i18n = I18n("zh")
        report = i18n.get_all("report")
        assert "title" in report
        assert "section_a" in report

    def test_get_all_report_section_en(self):
        """测试获取英文报告节"""
        i18n = I18n("en")
        report = i18n.get_all("report")
        assert "title" in report
        assert "section_a" in report

    def test_get_all_missing_section(self):
        """测试获取不存在的节"""
        i18n = I18n("zh")
        result = i18n.get_all("nonexistent")
        assert result == {}


class TestGlobalI18n:
    """测试全局 i18n 函数"""

    def test_get_i18n_default(self):
        """测试获取默认 i18n 实例"""
        i18n = get_i18n()
        assert i18n is not None
        assert i18n.locale in SUPPORTED_LOCALES

    def test_get_i18n_with_locale(self):
        """测试获取指定语言的 i18n 实例"""
        i18n = get_i18n("en")
        assert i18n.locale == "en"

    def test_set_locale(self):
        """测试设置全局语言"""
        set_locale("en")
        i18n = get_i18n()
        assert i18n.locale == "en"
        # 恢复
        set_locale("zh")

    def test_t_shortcut_zh(self):
        """测试 t() 快捷函数中文"""
        result = t("report.title", locale="zh")
        assert "青天" in result

    def test_t_shortcut_en(self):
        """测试 t() 快捷函数英文"""
        result = t("report.title", locale="en")
        assert "Qingtian" in result

    def test_t_shortcut_with_global(self):
        """测试 t() 使用全局语言"""
        set_locale("zh")
        result = t("report.title")
        assert "青天" in result


class TestEnvironmentLocale:
    """测试环境变量设置"""

    def test_env_locale_setting(self):
        """测试通过环境变量设置语言"""
        import app.i18n as i18n_module

        # 重置全局实例
        i18n_module._global_i18n = None

        with patch.dict(os.environ, {"QINGTIAN_LOCALE": "en"}):
            i18n = get_i18n()
            assert i18n.locale == "en"

        # 恢复
        i18n_module._global_i18n = None


class TestLoadLocaleData:
    """测试加载语言数据"""

    def test_load_zh_data(self):
        """测试加载中文数据"""
        # 清除缓存
        load_locale_data.cache_clear()
        data = load_locale_data("zh")
        assert "report" in data
        assert "scoring" in data

    def test_load_en_data(self):
        """测试加载英文数据"""
        load_locale_data.cache_clear()
        data = load_locale_data("en")
        assert "report" in data
        assert "scoring" in data

    def test_load_invalid_locale_fallback(self):
        """测试无效语言回退"""
        load_locale_data.cache_clear()
        data = load_locale_data("invalid")
        # 应该回退到默认语言的数据
        assert "report" in data


class TestSupportedLocales:
    """测试支持的语言"""

    def test_get_supported_locales(self):
        """测试获取支持的语言列表"""
        locales = get_supported_locales()
        assert "zh" in locales
        assert "en" in locales

    def test_is_supported_locale_zh(self):
        """测试中文是支持的"""
        assert is_supported_locale("zh") is True

    def test_is_supported_locale_en(self):
        """测试英文是支持的"""
        assert is_supported_locale("en") is True

    def test_is_supported_locale_invalid(self):
        """测试无效语言不支持"""
        assert is_supported_locale("invalid") is False


class TestDimensionTranslations:
    """测试维度相关翻译"""

    def test_dimension_zh(self):
        """测试中文维度翻译"""
        i18n = I18n("zh")
        assert "定义要点" in i18n.t("dimension.definition_points")
        assert "缺陷" in i18n.t("dimension.defects")
        assert "改进" in i18n.t("dimension.improvements")
        assert "证据" in i18n.t("dimension.evidence")

    def test_dimension_en(self):
        """测试英文维度翻译"""
        i18n = I18n("en")
        assert "Key Points" in i18n.t("dimension.definition_points")
        assert "Defects" in i18n.t("dimension.defects")
        assert "Improvements" in i18n.t("dimension.improvements")
        assert "Evidence" in i18n.t("dimension.evidence")


class TestPenaltyTranslations:
    """测试扣分项翻译"""

    def test_penalty_zh(self):
        """测试中文扣分项翻译"""
        i18n = I18n("zh")
        assert "扣" in i18n.t("penalty.deduct")
        assert "原因" in i18n.t("penalty.reason")
        assert "青天评语" in i18n.t("penalty.qingtian_comment")

    def test_penalty_en(self):
        """测试英文扣分项翻译"""
        i18n = I18n("en")
        assert "Deduct" in i18n.t("penalty.deduct")
        assert "Reason" in i18n.t("penalty.reason")
        assert "Qingtian Comment" in i18n.t("penalty.qingtian_comment")


class TestTagTranslations:
    """测试标签翻译"""

    def test_tags_zh(self):
        """测试中文标签翻译"""
        i18n = I18n("zh")
        assert "参数" in i18n.t("tags.missing_param")
        assert "频次" in i18n.t("tags.missing_freq")
        assert "验收" in i18n.t("tags.missing_acceptance")
        assert "责任" in i18n.t("tags.missing_role")

    def test_tags_en(self):
        """测试英文标签翻译"""
        i18n = I18n("en")
        assert "parameters" in i18n.t("tags.missing_param")
        assert "frequency" in i18n.t("tags.missing_freq")
        assert "acceptance" in i18n.t("tags.missing_acceptance")
        assert "responsibility" in i18n.t("tags.missing_role")


class TestApiTranslations:
    """测试 API 响应翻译"""

    def test_api_zh(self):
        """测试中文 API 翻译"""
        i18n = I18n("zh")
        assert "配置已重新加载" in i18n.t("api.config_reloaded")
        assert "项目不存在" in i18n.t("api.project_not_found")

    def test_api_en(self):
        """测试英文 API 翻译"""
        i18n = I18n("en")
        assert "Configuration reloaded" in i18n.t("api.config_reloaded")
        assert "Project not found" in i18n.t("api.project_not_found")


class TestTemplateTranslations:
    """测试模板动作翻译"""

    def test_templates_zh(self):
        """测试中文模板翻译"""
        i18n = I18n("zh")
        t07 = i18n.t("templates.template_07")
        assert "技术负责人" in t07
        assert "危大清单" in t07

    def test_templates_en(self):
        """测试英文模板翻译"""
        i18n = I18n("en")
        t07 = i18n.t("templates.template_07")
        assert "Technical Manager" in t07
        assert "hazard list" in t07
