"""Dependency-free accessibility contracts; browser layout gates live in scripts/."""

from html.parser import HTMLParser
from pathlib import Path
import unittest


class ShellElements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.dialog_bodies = []
        self.dialog_label = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "dialog":
            self.dialog_label = attrs.get("aria-labelledby")
        if "settings-panel__body" in attrs.get("class", "").split():
            self.dialog_bodies.append((self.dialog_label, attrs))

    def handle_endtag(self, tag):
        if tag == "dialog":
            self.dialog_label = None


class ScrollAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = ShellElements((Path(__file__).resolve().parents[1] / "ui" / "index.html").read_text(encoding="utf-8"))

    def test_every_dialog_body_is_a_labelled_keyboard_scroll_region(self):
        ids = {attrs.get("id") for _, attrs in self.shell.elements}
        dialogs = [attrs for tag, attrs in self.shell.elements if tag == "dialog"]
        self.assertEqual(len(dialogs), 11)
        self.assertEqual(len(self.shell.dialog_bodies), len(dialogs))
        for title, body in self.shell.dialog_bodies:
            with self.subTest(title=title):
                self.assertIn(title, ids)
                self.assertEqual(body.get("aria-labelledby"), title)
                self.assertEqual(body.get("role"), "region")
                self.assertEqual(body.get("tabindex"), "0")

    def test_nested_scroll_regions_are_named_and_keyboard_reachable(self):
        by_id = {attrs["id"]: attrs for _, attrs in self.shell.elements if "id" in attrs}
        for region in ("historyVaultList", "jobLog", "standingsEditor", "leaderboardEditor", "salaryOfferEditor", "conversationList", "preservedList", "settingsPreservedList", "worldContextList", "worldCounterpartList", "directorImages", "directorMemories", "directorTurns"):
            with self.subTest(region=region):
                self.assertEqual(by_id[region].get("tabindex"), "0")
                self.assertEqual(by_id[region].get("role"), "region")
                self.assertTrue(by_id[region].get("aria-label"))


class OfflineChatContractTests(unittest.TestCase):
    """Master plan first-slice step 8: chat no longer requires an LLM."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "ui"
        cls.render = (root / "js" / "render.js").read_text(encoding="utf-8")
        cls.app = (root / "js" / "app.js").read_text(encoding="utf-8")
        cls.html = (root / "index.html").read_text(encoding="utf-8")
        cls.css = (root / "css" / "app.css").read_text(encoding="utf-8")
        cls.presentation = (root.parents[0] / "presentation.py").read_text(encoding="utf-8")

    def test_chat_button_is_not_gated_by_llm_reachability(self):
        self.assertNotIn("chatButton.disabled = !llmReady || !open", self.render)
        self.assertNotIn("!dashboard()?.app?.llm_reachable", self.app)
        self.assertIn("renderer_preference", self.app)
        self.assertIn("confirm_turn_id", self.app)

    def test_optional_gemini_controls_do_not_echo_a_saved_secret(self):
        for element_id in (
            "aiProviderInput",
            "geminiModelInput",
            "geminiApiKeyInput",
            "geminiConsentInput",
            "geminiTestButton",
            "geminiClearButton",
            "apiDot",
            "apiStatus",
            "geminiUsagePanel",
            "geminiTodayCalls",
            "geminiTotalTokens",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn('/api/v1/providers/gemini/test', self.app)
        self.assertIn('/api/v1/providers/gemini/auto-activate', self.app)
        self.assertIn('config.gemini_key_present', self.app)
        self.assertNotIn('config.gemini_api_key', self.app)
        self.assertIn('provider === "gemini"', self.app)
        self.assertIn('Gemini API 켜짐', self.render)
        self.assertIn('usageMetadata', (Path(__file__).resolve().parents[1] / "gemini_provider.py").read_text(encoding="utf-8"))

    def test_settings_state_that_local_models_are_never_started_automatically(self):
        self.assertIn("실행 중인 로컬 LLM 우선 · 없으면 내장 엔진", self.html)
        self.assertIn("로컬 LLM을 자동으로 실행하지 않습니다", self.html)
        self.assertIn("저장된 경로는 실행 동의가 아니며", self.html)
        for element_id in (
            "localLlmLauncherInput", "localLlmStartButton", "localLlmStopButton",
            "localLlmRefreshButton", "localLlmControlState", "localLlmControlMessage",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn('/api/v1/providers/local/status', self.app)
        self.assertIn('/api/v1/providers/local/start', self.app)
        self.assertIn('/api/v1/providers/local/stop', self.app)
        self.assertIn('confirmed: true', self.app)
        self.assertIn('GPU·메모리를 많이 사용할 수 있으며', self.app)
        self.assertIn('외부에서 켠 서버에는 영향을 주지 않습니다', self.app)

    def test_gemini_can_render_starplayer_chat_and_explicit_image_review(self):
        self.assertIn('payload.category === "starplayer" && provider !== "gemini"', self.app)
        self.assertIn('"Gemini와 이 만남 이어가기"', self.render)
        self.assertIn('"Gemini로 이미지 해석"', self.app)
        self.assertIn('provider, consent: true', self.app)
        self.assertIn('현재 이미지 1장을 Google Gemini API로 전송', self.app)
        self.assertIn('다시 확인한 1장만 전송', self.html)

    def test_image_review_has_explicit_reaction_scope_and_heat_only_controls_tone(self):
        for element_id in ("attachmentUseLlmHintButton", "attachmentReactionHeat", "heatOutput"):
            self.assertIn(f'id="{element_id}"', self.html)
        for scope in ("private", "community", "public"):
            self.assertIn(f'name="attachmentReactionScope" value="{scope}"', self.html)
        self.assertIn("reaction_scope: attachmentReactionScope()", self.app)
        self.assertIn("useAttachmentLlmHint", self.app)
        self.assertIn("사실과 반응 수에는 영향 없음", self.app)
        self.assertIn("맵기 농도", self.html)

    def test_whole_feed_has_one_provider_job_and_text_social_surfaces(self):
        for element_id in (
            "enrichFeedButton", "enrichFeedButtonLabel", "feedProviderBadge",
            "enrichArticlesButton", "enrichArticlesButtonLabel", "articleProviderBadge",
            "enrichCommunityButton", "enrichCommunityButtonLabel",
            "socialList", "socialCount", "boardCount",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn('/api/v1/jobs/feed', self.app)
        self.assertIn('/api/v1/jobs/feed/articles', self.app)
        self.assertIn('/api/v1/jobs/feed/community', self.app)
        self.assertIn('reaction_bundle', self.render)
        self.assertIn('Gemini로 전체 함께 만들기', self.render)
        self.assertIn('로컬 LLM으로 전체 함께 만들기', self.render)
        self.assertIn('Gemini로 기사만 만들기', self.render)
        self.assertIn('Gemini로 커뮤니티·SNS만 만들기', self.render)
        self.assertIn('surface_sources?.articles?.renderer', self.render)
        self.assertIn('surface_sources?.community?.renderer', self.render)

    def test_local_override_notifications_and_language_controls_are_explicit(self):
        for element_id in (
            "localEnrichArticlesButton", "localEnrichCommunityButton", "localNarrativeButton",
            "languageLevelInput", "languageLevelOutput", "notificationsEnabledInput",
            "notificationTestButton", "notificationStatus", "communityCorpusState",
            "communityCorpusDetail",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        for route in (
            "/api/v1/jobs/feed/articles/local",
            "/api/v1/jobs/feed/community/local",
            "/api/v1/jobs/narrative/local",
        ):
            self.assertIn(route, self.app)
        self.assertIn('"Notification" in window', self.app)
        self.assertIn("requestNotificationAccess", self.app)
        self.assertIn("community_language_level", self.app)
        self.assertIn("runtime_web_search", self.app)
        self.assertIn('config.ai_provider !== "local_auto"', self.render)
        self.assertNotIn("startLocalModel();", self.app)

    def test_known_communities_and_social_streams_use_distinct_rendering_skins(self):
        for code, brand in (("dc", "디시인사이드"), ("fmk", "에펨코리아"), ("mlb", "MLBPARK")):
            self.assertIn(f'{code}: {{ mark:', self.render)
            self.assertIn(brand, self.render)
            self.assertIn(f'.board--{code}', self.css)
        for code in ("x", "threads", "instagram", "facebook", "japan-translation", "global-translation"):
            self.assertIn(f'.social-card--{code}', self.css)
        self.assertIn('data-community-skin', self.render)
        self.assertIn('data-social-skin', self.render)
        self.assertIn('board__columns', self.render)
        self.assertIn('social-reply__rail', self.render)
        self.assertEqual(2, self.render.count('boards.map(renderBoardCard).join'))
        self.assertEqual(2, self.render.count('social.map(renderSocialCard).join'))

    def test_standalone_feed_html_preserves_platform_specific_forum_skin(self):
        for code, brand in (("dc", "디시인사이드"), ("fmk", "에펨코리아"), ("mlb", "MLBPARK")):
            self.assertIn(f'"{code}":', self.presentation)
            self.assertIn(brand, self.presentation)
            self.assertIn(f'.th.{code}', self.presentation)
        self.assertIn('class="branch"', self.presentation)
        self.assertIn('class="when"', self.presentation)

    def test_shell_has_conversation_relink_and_preserved_regions(self):
        for element_id in ("conversationList", "storyPropsList", "storyRelink", "storyRelinkButton", "preservedList", "conversationCount"):
            self.assertIn(f'id="{element_id}"', self.html, element_id)
        self.assertIn("renderPreservedUniverses", self.render)
        self.assertIn("/relink/confirm", self.app)
        self.assertIn("/api/v1/universes/", self.app)

    def test_worldbook_controls_keep_context_and_identity_separate(self):
        for element_id in (
            "storyWorldbook", "worldContextBasis", "worldContextVisibility",
            "worldContextRemoteAllowed", "worldCounterpartName", "worldCounterpartRole",
            "storyCounterpart",
        ):
            self.assertIn(f'id="{element_id}"', self.html, element_id)
        for route in (
            "/api/v1/worldbook/context", "/api/v1/worldbook/context/retire",
            "/api/v1/worldbook/counterpart", "/api/v1/worldbook/counterpart/retire",
        ):
            self.assertIn(route, self.app)
        self.assertIn("participant_entity_id", self.app)

    def test_live_settings_expose_museum_and_dates_are_not_truncated(self):
        settings = self.html.split('id="settingsDialog"', 1)[1].split("</dialog>", 1)[0]
        self.assertIn('id="settingsPreservedList"', settings)
        self.assertIn('renderPreservedUniverses(payload, "settingsPreservedList")', self.app)
        dates = self.render.split("export function renderMuseumDates", 1)[1].split("function currency", 1)[0]
        self.assertNotIn("slice(", dates)
        self.assertIn("querySelectorAll", dates)
        self.assertIn("target.tabIndex = 0", dates)


if __name__ == "__main__":
    unittest.main()
