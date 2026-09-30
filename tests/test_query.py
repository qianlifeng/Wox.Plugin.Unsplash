import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, patch
from urllib.error import HTTPError

from wox_plugin import (
    ActionContext,
    Context,
    InvokePluginToolHandlerOption,
    PluginInitParams,
    Query,
    QueryElement,
    QueryEnv,
    QueryHint,
    QueryType,
    Selection,
    WoxImageType,
    WoxPreviewType,
)

from src.main import UnsplashAPIError, UnsplashClient, UnsplashPlugin, set_wallpaper_for_platform


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_I18N = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))["I18n"]


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def read(self):
        return self.body


class FakeAPI:
    def __init__(self, settings=None, locale="en_US"):
        self.settings = settings or {}
        self.notifications = []
        self.toolbar_messages = []
        self.cleared_toolbar_messages = []
        self.tools = {}
        self.commands = []
        self.changed_queries = []
        self.refreshed_queries = []
        self.translations = dict(PLUGIN_I18N[locale])

    async def register_plugin_tool(self, _ctx, option):
        from wox_plugin import RegisterPluginToolResult

        self.tools[option.tool.name] = option
        return RegisterPluginToolResult()

    async def get_setting(self, _ctx, key):
        return self.settings.get(key, "")

    async def notify(self, _ctx, message):
        self.notifications.append(message)

    async def show_toolbar_msg(self, _ctx, msg):
        self.toolbar_messages.append(msg)

    async def clear_toolbar_msg(self, _ctx, toolbar_msg_id):
        self.cleared_toolbar_messages.append(toolbar_msg_id)

    async def on_setting_changed(self, _ctx, _callback):
        return None

    async def register_query_commands(self, _ctx, commands):
        self.commands.extend(commands)

    async def change_query(self, _ctx, query):
        self.changed_queries.append(query)

    async def refresh_query(self, ctx, param):
        self.refreshed_queries.append((ctx, param))

    async def get_translation(self, _ctx, key):
        return self.translations.get(key, key)


class FakeUnsplashClient:
    def __init__(self, photos=None):
        self.photos = photos or []
        self.search_calls = []
        self.topic_calls = []
        self.tracked = []

    async def search_photos(self, **kwargs):
        self.search_calls.append(kwargs)
        return self.photos

    async def topic_photos(self, **kwargs):
        self.topic_calls.append(kwargs)
        return self.photos

    async def track_download(self, access_key, download_location):
        self.tracked.append((access_key, download_location))


class FakeDownloader:
    def __init__(self, output_path):
        self.output_path = output_path
        self.calls = []

    async def download(self, access_key, url, directory, filename):
        self.calls.append((access_key, url, directory, filename))
        return self.output_path


def make_query(search):
    return Query(
        id="query-1",
        type=QueryType.INPUT,
        raw_query=f"unsplash {search}",
        selection=Selection(),
        env=QueryEnv(),
        trigger_keyword="unsplash",
        command="",
        search=search,
    )


def make_command_query(command, search):
    return Query(
        id="query-1",
        type=QueryType.INPUT,
        raw_query=f"unsplash {command} {search}".strip(),
        selection=Selection(),
        env=QueryEnv(),
        trigger_keyword="unsplash",
        command=command,
        search=search,
    )


def sample_photo():
    return {
        "id": "photo-1",
        "description": "A mountain lake",
        "alt_description": "mountain lake under sky",
        "width": 4000,
        "height": 2500,
        "urls": {
            "thumb": "https://images.unsplash.com/thumb.jpg",
            "regular": "https://images.unsplash.com/regular.jpg",
            "full": "https://images.unsplash.com/full.jpg",
        },
        "links": {
            "html": "https://unsplash.com/photos/photo-1",
            "download_location": "https://api.unsplash.com/photos/photo-1/download",
        },
        "user": {
            "name": "Ada Lovelace",
            "links": {"html": "https://unsplash.com/@ada"},
        },
    }


class TestUnsplashPlugin(unittest.IsolatedAsyncioTestCase):
    async def test_featured_refresh_replaces_loading_and_reports_failures(self):
        client = FakeUnsplashClient([sample_photo()])
        plugin = UnsplashPlugin(client=client)
        api = FakeAPI({"access_key": "key"})
        ctx = Context.new()
        await plugin.init(ctx, PluginInitParams(api=api, plugin_directory="."))
        response = await plugin.query(ctx, make_query(""))
        self.assertEqual(response.results[0].title, "Featured wallpapers are loading")
        await plugin.refresh_featured_wallpaper_cache(ctx)
        self.assertEqual(len(api.refreshed_queries), 1)
        self.assertIs(api.refreshed_queries[0][0], ctx)
        response = await plugin.query(ctx, make_query(""))
        self.assertIsNotNone(response.layout.grid_layout)
        client.photos = []
        await plugin.refresh_featured_wallpaper_cache(ctx)
        response = await plugin.query(ctx, make_query(""))
        self.assertEqual(response.results[0].title, "No Unsplash photos found")
        client.topic_photos = AsyncMock(side_effect=UnsplashAPIError(401, "Unauthorized"))
        await plugin.refresh_featured_wallpaper_cache(ctx)
        response = await plugin.query(ctx, make_query(""))
        self.assertIn("invalid", response.results[0].sub_title)
        self.assertIsNone(response.layout.grid_layout)
        self.assertEqual(response.results[0].preview.preview_data, "")
        client.topic_photos = AsyncMock(return_value=[sample_photo()])
        await plugin.refresh_featured_wallpaper_cache(ctx)
        self.assertIsNone(plugin.featured_error)
        response = await plugin.query(ctx, make_query(""))
        self.assertIsNotNone(response.layout.grid_layout)
        await plugin.query(ctx, make_command_query("search", "ocean"))
        count = len(api.refreshed_queries)
        await plugin.refresh_featured_wallpaper_cache(ctx)
        self.assertEqual(len(api.refreshed_queries), count)

    async def test_only_photo_results_use_grid_layout(self):
        client = FakeUnsplashClient([sample_photo()])
        plugin = UnsplashPlugin(client=client)
        api = FakeAPI({"access_key": "key"})
        ctx = Context.new()
        await plugin.init(ctx, PluginInitParams(api=api, plugin_directory="."))
        response = await plugin.query(ctx, make_command_query("search", "ocean"))
        self.assertIsNotNone(response.layout.grid_layout)
        client.photos = []
        for query in (make_command_query("search", "ocean"), make_command_query("search", ""), make_query("su"), make_query("")):
            with self.subTest(query=query.raw_query):
                response = await plugin.query(ctx, query)
                self.assertTrue(response.results[0].title)
                self.assertEqual(response.results[0].preview.preview_data, "")
                self.assertIsNone(response.layout.grid_layout)
                self.assertNotIn("GridLayout", json.loads(response.to_json())["Layout"])
        client.search_photos = AsyncMock(side_effect=UnsplashAPIError(429, "Rate limited"))
        response = await plugin.query(ctx, make_command_query("search", "ocean"))
        self.assertIsNone(response.layout.grid_layout)
        self.assertEqual(response.results[0].preview.preview_data, "")
        api.settings["access_key"] = ""
        response = await plugin.query(ctx, make_query(""))
        self.assertIsNone(response.layout.grid_layout)
        self.assertEqual(response.results[0].preview.preview_data, "")
        api.settings["access_key"] = "key"
        plugin.latest_wallpapers = [sample_photo()]
        response = await plugin.query(ctx, make_query(""))
        self.assertIsNotNone(response.layout.grid_layout)

    async def test_query_hints_and_refinements_preserve_legacy_search(self):
        client = FakeUnsplashClient([sample_photo()])
        plugin = UnsplashPlugin(client=client)
        await plugin.init(Context.new(), PluginInitParams(api=FakeAPI({"access_key": "key"}), plugin_directory="."))
        query = make_command_query("search", "legacy text")
        query.query_hint = QueryHint(elements=[QueryElement(id="keywords", kind="argument", value="  blue sky  ")])
        query.refinements = {"orientation": "portrait", "content_filter": "high"}
        for system, modifier in (("Darwin", "cmd"), ("Windows", "ctrl"), ("Linux", "ctrl")):
            with patch("src.main.platform.system", return_value=system):
                response = await plugin.query(Context.new(), query)
            self.assertEqual(client.search_calls[-1]["query"], "blue sky")
            self.assertEqual(client.search_calls[-1]["orientation"], "portrait")
            self.assertEqual(client.search_calls[-1]["content_filter"], "high")
            self.assertEqual([r.hotkey for r in response.refinements], [f"{modifier}+o", f"{modifier}+h"])
            self.assertEqual(response.layout.grid_layout.columns, 4)
            self.assertEqual(json.loads(response.to_json())["Results"][0]["Id"], "photo-1")
        query.query_hint.elements[0].value = "   "
        calls = len(client.search_calls)
        await plugin.query(Context.new(), query)
        self.assertEqual(len(client.search_calls), calls)
        query.query_hint = None
        query.refinements = {"orientation": "invalid", "content_filter": "invalid"}
        await plugin.query(Context.new(), query)
        self.assertEqual(client.search_calls[-1]["query"], "legacy text")
        self.assertEqual(client.search_calls[-1]["orientation"], "landscape")
        self.assertEqual(client.search_calls[-1]["content_filter"], "low")

    async def test_tools_share_search_and_wallpaper_operations_without_launcher_ui(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeUnsplashClient([sample_photo()])
            client.get_photo = AsyncMock(return_value=sample_photo())
            downloader = FakeDownloader(Path(temp_dir) / "photo-1.jpg")
            wallpaper = Mock()
            api = FakeAPI({"access_key": "key", "download_dir": temp_dir})
            plugin = UnsplashPlugin(client=client, downloader=downloader, wallpaper_setter=wallpaper)
            ctx = Context.new()
            await plugin.init(ctx, PluginInitParams(api=api, plugin_directory="."))
            self.assertEqual(set(api.tools), {"search_photos", "set_wallpaper"})
            search = api.tools["search_photos"]
            result = await search.handler(ctx, InvokePluginToolHandlerOption(arguments={"query": "ocean", "per_page": 3}))
            self.assertEqual(result.output["photos"][0]["id"], "photo-1")
            self.assertEqual(client.search_calls[-1]["per_page"], 3)
            self.assertTrue(search.tool.annotations.read_only)
            tool = api.tools["set_wallpaper"]
            result = await tool.handler(ctx, InvokePluginToolHandlerOption(arguments={"photo_id": "photo-1"}))
            self.assertIsNone(result.error)
            self.assertEqual(result.output, {"path": str(downloader.output_path)})
            client.get_photo.assert_awaited_once_with("key", "photo-1")
            self.assertEqual(client.tracked, [("key", sample_photo()["links"]["download_location"])])
            wallpaper.assert_called_once_with(downloader.output_path)
            self.assertFalse(tool.tool.annotations.read_only)
            self.assertEqual(api.notifications + api.toolbar_messages + api.changed_queries, [])
            for photo_id in ("", "../outside", "a/b"):
                result = await tool.handler(ctx, InvokePluginToolHandlerOption(arguments={"photo_id": photo_id}))
                self.assertEqual(result.error.code, "EXECUTION_FAILED")
            self.assertEqual(client.get_photo.await_count, 1)
            for arguments in ({"query": " "}, {"query": "ocean"}):
                api.settings["access_key"] = ""
                result = await search.handler(ctx, InvokePluginToolHandlerOption(arguments=arguments))
                self.assertIsNone(result.output)
                self.assertEqual(result.error.code, "EXECUTION_FAILED")
            api.settings["access_key"] = "key"
            client.search_photos = AsyncMock(side_effect=UnsplashAPIError(429, "Rate limited"))
            result = await search.handler(ctx, InvokePluginToolHandlerOption(arguments={"query": "ocean"}))
            self.assertEqual(result.error.message, "Rate limited")
            wallpaper.side_effect = RuntimeError("Wallpaper failed")
            result = await tool.handler(ctx, InvokePluginToolHandlerOption(arguments={"photo_id": "photo-1"}))
            self.assertIsNone(result.output)
            self.assertEqual(result.error.message, "Wallpaper failed")

    async def test_init_does_not_register_visible_query_commands(self):
        plugin = UnsplashPlugin(client=FakeUnsplashClient())
        api = FakeAPI()

        await plugin.init(Context.new(), PluginInitParams(api=api, plugin_directory="."))

        self.assertEqual(api.commands, [])

    async def test_empty_query_without_access_key_still_guides_user_to_configure_key(self):
        plugin = UnsplashPlugin(client=FakeUnsplashClient())
        api = FakeAPI()
        await plugin.init(Context.new(), PluginInitParams(api=api, plugin_directory="."))

        results = (await plugin.query(Context.new(), make_query(""))).results

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "i18n:result_access_key_title")
        self.assertEqual(results[0].sub_title, "i18n:result_access_key_subtitle")

    async def test_default_query_with_access_key_uses_cached_featured_wallpaper_groups(self):
        latest_photo = sample_photo()
        latest_photo["id"] = "latest-photo"
        popular_photo = sample_photo()
        popular_photo["id"] = "popular-photo"
        client = FakeUnsplashClient()
        client.topic_photos = AsyncMock(side_effect=[[latest_photo], [popular_photo]])
        plugin = UnsplashPlugin(client=client, start_background_refresh=False)
        api = FakeAPI({"access_key": "abc123"})
        await plugin.init(Context.new(), PluginInitParams(api=api, plugin_directory="."))
        await plugin.refresh_featured_wallpaper_cache(Context.new())

        results = (await plugin.query(Context.new(), make_query(""))).results

        self.assertEqual([result.id for result in results], ["latest-photo", "popular-photo"])
        self.assertEqual(results[0].group, "i18n:group_latest_wallpapers")
        self.assertEqual(results[1].group, "i18n:group_popular_wallpapers")
        self.assertGreater(results[0].group_score, results[1].group_score)
        self.assertEqual(client.search_calls, [])
        client.topic_photos.assert_has_awaits(
            [
                call(
                    access_key="abc123",
                    topic="wallpapers",
                    per_page=12,
                    orientation="landscape",
                    order_by="latest",
                ),
                call(
                    access_key="abc123",
                    topic="wallpapers",
                    per_page=12,
                    orientation="landscape",
                    order_by="popular",
                ),
            ]
        )

    async def test_default_query_with_empty_cache_does_not_call_unsplash(self):
        client = FakeUnsplashClient([sample_photo()])
        plugin = UnsplashPlugin(client=client, start_background_refresh=False)
        api = FakeAPI({"access_key": "abc123"})
        await plugin.init(Context.new(), PluginInitParams(api=api, plugin_directory="."))

        results = (await plugin.query(Context.new(), make_query(""))).results

        self.assertEqual(client.search_calls, [])
        self.assertEqual(client.topic_calls, [])
        self.assertEqual(results[0].title, "Featured wallpapers are loading")

    async def test_refresh_featured_wallpaper_cache_uses_latest_and_popular_wallpapers_topics(self):
        client = FakeUnsplashClient([sample_photo()])
        plugin = UnsplashPlugin(client=client, start_background_refresh=False)
        api = FakeAPI({"access_key": "abc123", "results_per_page": "8", "orientation": "landscape"})
        await plugin.init(Context.new(), PluginInitParams(api=api, plugin_directory="."))

        await plugin.refresh_featured_wallpaper_cache(Context.new())

        self.assertEqual(
            client.topic_calls,
            [
                {
                    "access_key": "abc123",
                    "topic": "wallpapers",
                    "per_page": 8,
                    "orientation": "landscape",
                    "order_by": "latest",
                },
                {
                    "access_key": "abc123",
                    "topic": "wallpapers",
                    "per_page": 8,
                    "orientation": "landscape",
                    "order_by": "popular",
                },
            ],
        )

    async def test_missing_access_key_guides_user_to_unsplash_developers(self):
        plugin = UnsplashPlugin(client=FakeUnsplashClient())
        api = FakeAPI()
        await plugin.init(Context.new(), PluginInitParams(api=api, plugin_directory="."))

        results = (await plugin.query(Context.new(), make_command_query("search", "forest"))).results

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "i18n:result_access_key_title")
        self.assertEqual(results[0].sub_title, "i18n:result_access_key_subtitle")
        self.assertEqual(results[0].preview.preview_data, "")

    async def test_search_request_settings_are_passed_to_unsplash_client(self):
        client = FakeUnsplashClient([sample_photo()])
        plugin = UnsplashPlugin(client=client)
        api = FakeAPI(
            {
                "access_key": "abc123",
                "results_per_page": "8",
                "orientation": "landscape",
                "content_filter": "high",
            }
        )
        await plugin.init(Context.new(), PluginInitParams(api=api, plugin_directory="."))

        await plugin.query(Context.new(), make_command_query("search", "ocean"))

        self.assertEqual(
            client.search_calls,
            [
                {
                    "access_key": "abc123",
                    "query": "ocean",
                    "per_page": 8,
                    "orientation": "landscape",
                    "content_filter": "high",
                }
            ],
        )

    async def test_photo_maps_to_result_with_attribution_preview_and_actions(self):
        plugin = UnsplashPlugin(client=FakeUnsplashClient([sample_photo()]))
        api = FakeAPI({"access_key": "abc123", "results_per_page": "12"})
        await plugin.init(Context.new(), PluginInitParams(api=api, plugin_directory="."))

        results = (await plugin.query(Context.new(), make_command_query("search", "mountains"))).results

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.id, "photo-1")
        self.assertEqual(result.icon.image_type, WoxImageType.URL)
        self.assertEqual(result.icon.image_data, "https://images.unsplash.com/thumb.jpg")
        self.assertIn("Photo by Ada Lovelace on Unsplash", result.sub_title)
        self.assertEqual(result.preview.preview_type, WoxPreviewType.IMAGE)
        self.assertEqual(result.preview.preview_data, "url:https://images.unsplash.com/regular.jpg")
        self.assertEqual([action.name for action in result.actions], ["i18n:action_set_wallpaper", "i18n:action_open_unsplash"])
        self.assertTrue(result.actions[0].is_default)
        self.assertTrue(result.actions[0].prevent_hide_after_action)

    async def test_api_errors_return_specific_user_messages(self):
        for status, expected in ((401, "invalid"), (403, "permission"), (429, "rate limit")):
            client = FakeUnsplashClient()
            client.search_photos = Mock(side_effect=UnsplashAPIError(status, "api error"))
            plugin = UnsplashPlugin(client=client)
            api = FakeAPI({"access_key": "bad-key"})
            await plugin.init(Context.new(), PluginInitParams(api=api, plugin_directory="."))

            results = (await plugin.query(Context.new(), make_command_query("search", "city"))).results

            self.assertIn(expected, results[0].sub_title.lower())

    async def test_search_empty_results_uses_chinese_translations(self):
        plugin = UnsplashPlugin(client=FakeUnsplashClient([]))
        api = FakeAPI({"access_key": "abc123"}, locale="zh_CN")
        await plugin.init(Context.new(), PluginInitParams(api=api, plugin_directory="."))

        results = (await plugin.query(Context.new(), make_command_query("search", "海边"))).results

        self.assertEqual(results[0].title, "未找到 Unsplash 图片")
        self.assertEqual(results[0].sub_title, "没有找到“海边”的相关结果。")

    async def test_plain_text_after_trigger_does_not_search(self):
        client = FakeUnsplashClient([sample_photo()])
        plugin = UnsplashPlugin(client=client)
        api = FakeAPI({"access_key": "abc123"})
        await plugin.init(Context.new(), PluginInitParams(api=api, plugin_directory="."))

        results = (await plugin.query(Context.new(), make_query("ocean"))).results

        self.assertEqual(client.search_calls, [])
        self.assertEqual(results[0].sub_title, "Type unsplash search ocean to search Unsplash wallpapers.")

    async def test_set_wallpaper_action_tracks_download_before_downloading(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "photo-1.jpg"
            output_path.write_bytes(b"jpg")
            client = FakeUnsplashClient()
            downloader = FakeDownloader(output_path)
            wallpaper = Mock()
            plugin = UnsplashPlugin(client=client, downloader=downloader, wallpaper_setter=wallpaper)
            api = FakeAPI({"access_key": "abc123", "download_dir": temp_dir})
            await plugin.init(Context.new(), PluginInitParams(api=api, plugin_directory="."))

            await plugin.set_wallpaper(
                Context.new(),
                ActionContext(
                    result_id="photo-1",
                    result_action_id="set-wallpaper",
                    context_data={
                        "id": "photo-1",
                        "full_url": "https://images.unsplash.com/full.jpg",
                        "download_location": "https://api.unsplash.com/photos/photo-1/download",
                    },
                ),
            )

            self.assertEqual(client.tracked, [("abc123", "https://api.unsplash.com/photos/photo-1/download")])
            self.assertEqual(downloader.calls[0][1], "https://images.unsplash.com/full.jpg")
            wallpaper.assert_called_once_with(output_path)
            self.assertIn("Wallpaper updated", api.notifications[-1])
            self.assertEqual([message.id for message in api.toolbar_messages], ["unsplash-wallpaper-photo-1"] * 2)
            self.assertEqual(
                [message.title for message in api.toolbar_messages],
                ["Downloading wallpaper...", "Setting wallpaper..."],
            )
            self.assertEqual(api.cleared_toolbar_messages, ["unsplash-wallpaper-photo-1"])


class TestUnsplashClient(unittest.IsolatedAsyncioTestCase):
    async def test_get_photo_uses_official_endpoint(self):
        opener = Mock(return_value=FakeResponse(json.dumps(sample_photo()).encode()))
        photo = await UnsplashClient(opener=opener).get_photo("key", "photo-1")
        self.assertEqual(photo["id"], "photo-1")
        self.assertEqual(opener.call_args.args[0].full_url, "https://api.unsplash.com/photos/photo-1")

    async def test_search_photos_builds_official_request_shape(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            body = json.dumps({"results": [sample_photo()]}).encode("utf-8")
            return FakeResponse(body)

        client = UnsplashClient(opener=opener)

        photos = await client.search_photos(
            access_key="abc123",
            query="blue sky",
            per_page=9,
            orientation="landscape",
            content_filter="low",
        )

        self.assertEqual(photos[0]["id"], "photo-1")
        self.assertIn("https://api.unsplash.com/search/photos?", captured["url"])
        self.assertIn("query=blue+sky", captured["url"])
        self.assertIn("per_page=9", captured["url"])
        self.assertIn("orientation=landscape", captured["url"])
        self.assertEqual(captured["headers"]["Authorization"], "Client-ID abc123")
        self.assertEqual(captured["headers"]["Accept-version"], "v1")
        self.assertEqual(captured["timeout"], 15)

    async def test_search_photos_raises_status_specific_api_error(self):
        def opener(_request, timeout):
            self.assertEqual(timeout, 15)
            raise HTTPError("https://api.unsplash.com/search/photos", 429, "Too Many Requests", {}, None)

        client = UnsplashClient(opener=opener)

        with self.assertRaises(UnsplashAPIError) as raised:
            await client.search_photos(
                access_key="abc123",
                query="city",
                per_page=12,
                orientation="landscape",
                content_filter="low",
            )

        self.assertEqual(raised.exception.status_code, 429)

    async def test_topic_photos_builds_wallpapers_topic_request_shape(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            body = json.dumps([sample_photo()]).encode("utf-8")
            return FakeResponse(body)

        client = UnsplashClient(opener=opener)

        photos = await client.topic_photos(
            access_key="abc123",
            topic="wallpapers",
            per_page=12,
            orientation="landscape",
            order_by="popular",
        )

        self.assertEqual(photos[0]["id"], "photo-1")
        self.assertIn("https://api.unsplash.com/topics/wallpapers/photos?", captured["url"])
        self.assertIn("per_page=12", captured["url"])
        self.assertIn("orientation=landscape", captured["url"])
        self.assertIn("order_by=popular", captured["url"])
        self.assertEqual(captured["headers"]["Authorization"], "Client-ID abc123")
        self.assertEqual(captured["headers"]["Accept-version"], "v1")
        self.assertEqual(captured["timeout"], 15)


class TestWallpaperPlatforms(unittest.TestCase):
    def test_windows_wallpaper_uses_system_parameters_info(self):
        with patch("src.main.platform.system", return_value="Windows"), patch("src.main.ctypes.windll", create=True) as windll:
            windll.user32.SystemParametersInfoW.return_value = True

            set_wallpaper_for_platform(Path("C:/wall/photo.jpg"))

            windll.user32.SystemParametersInfoW.assert_called()

    def test_macos_wallpaper_uses_osascript(self):
        with patch("src.main.platform.system", return_value="Darwin"), patch("src.main.subprocess.run") as run:
            set_wallpaper_for_platform(Path("/tmp/photo.jpg"))

            command = run.call_args.args[0]
            self.assertEqual(command[0], "osascript")
            self.assertIn("/tmp/photo.jpg", command[-1])

    def test_linux_gnome_wallpaper_uses_gsettings(self):
        with (
            patch("src.main.platform.system", return_value="Linux"),
            patch.dict("src.main.os.environ", {"XDG_CURRENT_DESKTOP": "GNOME"}),
            patch("src.main.subprocess.run") as run,
        ):
            set_wallpaper_for_platform(Path("/tmp/photo.jpg"))

            self.assertEqual(run.call_args.args[0][0], "gsettings")
            self.assertIn("file:///tmp/photo.jpg", run.call_args.args[0])

    def test_linux_unknown_desktop_raises_clear_error(self):
        with patch("src.main.platform.system", return_value="Linux"), patch.dict("src.main.os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Unsupported Linux desktop"):
                set_wallpaper_for_platform(Path("/tmp/photo.jpg"))


if __name__ == "__main__":
    unittest.main()
