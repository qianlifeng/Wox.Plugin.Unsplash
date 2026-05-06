import asyncio
import ctypes
import json
import os
import platform
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable

from wox_plugin import (
    ActionContext,
    Context,
    Plugin,
    PluginInitParams,
    PublicAPI,
    Query,
    Result,
    ResultAction,
    ToolbarMsg,
    WoxImage,
    WoxPreview,
    WoxPreviewType,
)

UNSPLASH_DEVELOPERS_URL = "https://unsplash.com/developers"
UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"
UNSPLASH_TOPICS_URL = "https://api.unsplash.com/topics"
APP_DIR_NAME = "Wox.Plugin.Unsplash"
DEFAULT_RESULTS_PER_PAGE = 12
FEATURED_REFRESH_INTERVAL_SECONDS = 30 * 60
VALID_ORIENTATIONS = {"landscape", "portrait", "squarish"}
VALID_CONTENT_FILTERS = {"low", "high"}


class UnsplashAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class UnsplashClient:
    def __init__(self, opener: Callable[..., Any] | None = None):
        self.opener = opener or urllib.request.urlopen

    async def search_photos(
        self,
        *,
        access_key: str,
        query: str,
        per_page: int,
        orientation: str,
        content_filter: str,
    ) -> list[dict[str, Any]]:
        params = {
            "query": query,
            "per_page": str(per_page),
            "content_filter": content_filter,
        }
        if orientation in VALID_ORIENTATIONS:
            params["orientation"] = orientation

        data = await self._request_json(UNSPLASH_SEARCH_URL, access_key, params)
        if not isinstance(data, dict):
            return []
        results = data.get("results", [])
        return results if isinstance(results, list) else []

    async def topic_photos(
        self,
        *,
        access_key: str,
        topic: str,
        per_page: int,
        orientation: str,
        order_by: str,
    ) -> list[dict[str, Any]]:
        params = {
            "per_page": str(per_page),
            "order_by": order_by,
        }
        if orientation in VALID_ORIENTATIONS:
            params["orientation"] = orientation

        topic_slug = urllib.parse.quote(topic.strip(), safe="")
        data = await self._request_json(f"{UNSPLASH_TOPICS_URL}/{topic_slug}/photos", access_key, params)
        return data if isinstance(data, list) else []

    async def track_download(self, access_key: str, download_location: str) -> None:
        if not download_location:
            return
        await self._request_json(download_location, access_key, {})

    async def _request_json(self, url: str, access_key: str, params: dict[str, str]) -> Any:
        request_url = url
        if params:
            request_url = f"{url}?{urllib.parse.urlencode(params)}"

        request = urllib.request.Request(
            request_url,
            headers={
                "Authorization": f"Client-ID {access_key}",
                "Accept-Version": "v1",
                "User-Agent": "Wox.Plugin.Unsplash/0.0.1",
            },
        )

        def do_request() -> dict[str, Any]:
            try:
                with self.opener(request, timeout=15) as response:
                    raw = response.read().decode("utf-8")
            except urllib.error.HTTPError as error:
                raise UnsplashAPIError(error.code, error.reason) from error
            except urllib.error.URLError as error:
                raise UnsplashAPIError(0, str(error.reason)) from error

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as error:
                raise UnsplashAPIError(0, "Unsplash returned invalid JSON") from error
            return parsed

        return await asyncio.to_thread(do_request)


class ImageDownloader:
    async def download(self, access_key: str, url: str, directory: Path, filename: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        output_path = directory / filename
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Client-ID {access_key}",
                "Accept-Version": "v1",
                "User-Agent": "Wox.Plugin.Unsplash/0.0.1",
            },
        )

        def do_download() -> Path:
            with urllib.request.urlopen(request, timeout=30) as response:
                output_path.write_bytes(response.read())
            return output_path

        return await asyncio.to_thread(do_download)


def default_download_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return base / APP_DIR_NAME / "wallpapers"


def set_wallpaper_for_platform(image_path: Path) -> None:
    system = platform.system()
    if system == "Windows":
        _set_windows_wallpaper(image_path)
        return
    if system == "Darwin":
        _set_macos_wallpaper(image_path)
        return
    if system == "Linux":
        _set_linux_wallpaper(image_path)
        return
    raise RuntimeError(f"Unsupported platform: {system}")


def _set_windows_wallpaper(image_path: Path) -> None:
    image = str(image_path)
    winreg_module: Any | None
    try:
        import winreg as winreg_module
    except ImportError:
        winreg_module = None

    if winreg_module is not None:
        key_path = r"Control Panel\Desktop"
        with winreg_module.OpenKey(winreg_module.HKEY_CURRENT_USER, key_path, 0, winreg_module.KEY_SET_VALUE) as key:
            winreg_module.SetValueEx(key, "WallpaperStyle", 0, winreg_module.REG_SZ, "10")
            winreg_module.SetValueEx(key, "TileWallpaper", 0, winreg_module.REG_SZ, "0")

    spi_setdeskwallpaper = 20
    spif_update_ini_file = 0x01
    spif_send_change = 0x02
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        raise RuntimeError("Windows wallpaper API is unavailable")

    ok = windll.user32.SystemParametersInfoW(
        spi_setdeskwallpaper,
        0,
        image,
        spif_update_ini_file | spif_send_change,
    )
    if not ok:
        raise RuntimeError("Windows refused to update the wallpaper")


def _set_macos_wallpaper(image_path: Path) -> None:
    script = f'tell application "System Events" to set picture of every desktop to "{image_path.as_posix()}"'
    subprocess.run(["osascript", "-e", script], check=True)


def _set_linux_wallpaper(image_path: Path) -> None:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    image_uri = f"file://{image_path.as_posix()}"
    if "gnome" in desktop or "unity" in desktop or "cinnamon" in desktop:
        subprocess.run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", image_uri], check=True)
        subprocess.run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", image_uri], check=False)
        return
    if "kde" in desktop or "plasma" in desktop:
        subprocess.run(["plasma-apply-wallpaperimage", str(image_path)], check=True)
        return
    if "xfce" in desktop:
        raise RuntimeError("XFCE wallpaper setting is not supported yet because monitor property names vary")
    raise RuntimeError("Unsupported Linux desktop. Supported desktops: GNOME and KDE Plasma")


class UnsplashPlugin(Plugin):
    api: PublicAPI

    def __init__(
        self,
        *,
        client: Any | None = None,
        downloader: ImageDownloader | None = None,
        wallpaper_setter: Callable[[Path], None] | None = None,
        start_background_refresh: bool | None = None,
        featured_refresh_interval_seconds: int = FEATURED_REFRESH_INTERVAL_SECONDS,
    ):
        self.client = client or UnsplashClient()
        self.downloader = downloader or ImageDownloader()
        self.wallpaper_setter = wallpaper_setter or set_wallpaper_for_platform
        self.start_background_refresh = start_background_refresh if start_background_refresh is not None else client is None
        self.featured_refresh_interval_seconds = featured_refresh_interval_seconds
        self.latest_wallpapers: list[dict[str, Any]] = []
        self.popular_wallpapers: list[dict[str, Any]] = []
        self.featured_refresh_task: asyncio.Task[None] | None = None

    async def init(self, ctx: Context, init_params: PluginInitParams) -> None:
        self.api = init_params.api
        await self.api.on_setting_changed(ctx, self._on_setting_changed)
        if self.start_background_refresh and self.featured_refresh_task is None:
            self.featured_refresh_task = asyncio.create_task(self._refresh_featured_wallpapers_periodically(ctx))

    async def _on_setting_changed(self, _ctx: Context, _key: str, _value: str) -> None:
        if self.start_background_refresh and _key in {"access_key", "results_per_page", "orientation"}:
            asyncio.create_task(self.refresh_featured_wallpaper_cache(_ctx))
        return None

    async def query(self, ctx: Context, query: Query) -> list[Result]:
        search_text = query.search.strip()
        access_key = (await self.api.get_setting(ctx, "access_key")).strip()
        if not access_key:
            return [await self._access_key_result(ctx)]

        command = query.command.strip().lower()
        if command != "search":
            if query.search.strip():
                return [
                    await self._localized_message_result(
                        ctx,
                        "result_use_search_command_title",
                        "result_use_search_command_subtitle",
                    )
                ]
            return await self._cached_featured_wallpaper_results(ctx)

        if not search_text:
            return [await self._localized_message_result(ctx, "result_search_title", "result_search_subtitle")]

        try:
            photos = await self.client.search_photos(
                access_key=access_key,
                query=search_text,
                per_page=await self._results_per_page(ctx),
                orientation=await self._choice_setting(ctx, "orientation", "landscape", VALID_ORIENTATIONS),
                content_filter=await self._choice_setting(ctx, "content_filter", "low", VALID_CONTENT_FILTERS),
            )
        except UnsplashAPIError as error:
            return [await self._api_error_result(ctx, error)]

        if not photos:
            return [
                await self._localized_message_result(
                    ctx,
                    "result_no_photos_found_title",
                    "result_no_photos_found_subtitle",
                    query=search_text,
                )
            ]

        return [await self._photo_result(ctx, photo) for photo in photos]

    async def _cached_featured_wallpaper_results(self, ctx: Context) -> list[Result]:
        if not self.latest_wallpapers and not self.popular_wallpapers:
            return [
                await self._localized_message_result(
                    ctx,
                    "result_featured_loading_title",
                    "result_featured_loading_subtitle",
                )
            ]

        results = [
            await self._photo_result(ctx, photo, group="i18n:group_latest_wallpapers", group_score=200) for photo in self.latest_wallpapers
        ]
        results.extend(
            [
                await self._photo_result(ctx, photo, group="i18n:group_popular_wallpapers", group_score=100)
                for photo in self.popular_wallpapers
            ]
        )
        return results

    async def refresh_featured_wallpaper_cache(self, ctx: Context) -> None:
        access_key = (await self.api.get_setting(ctx, "access_key")).strip()
        if not access_key:
            return

        try:
            latest = await self.client.topic_photos(
                access_key=access_key,
                topic="wallpapers",
                per_page=await self._results_per_page(ctx),
                orientation=await self._choice_setting(ctx, "orientation", "landscape", VALID_ORIENTATIONS),
                order_by="latest",
            )
            popular = await self.client.topic_photos(
                access_key=access_key,
                topic="wallpapers",
                per_page=await self._results_per_page(ctx),
                orientation=await self._choice_setting(ctx, "orientation", "landscape", VALID_ORIENTATIONS),
                order_by="popular",
            )
        except UnsplashAPIError:
            return

        if latest:
            self.latest_wallpapers = latest
        if popular:
            self.popular_wallpapers = popular

    async def _refresh_featured_wallpapers_periodically(self, ctx: Context) -> None:
        while True:
            await self.refresh_featured_wallpaper_cache(ctx)
            await asyncio.sleep(self.featured_refresh_interval_seconds)

    async def _results_per_page(self, ctx: Context) -> int:
        raw = (await self.api.get_setting(ctx, "results_per_page")).strip()
        try:
            value = int(raw or DEFAULT_RESULTS_PER_PAGE)
        except ValueError:
            value = DEFAULT_RESULTS_PER_PAGE
        return max(1, min(value, 30))

    async def _choice_setting(self, ctx: Context, key: str, default: str, allowed: set[str]) -> str:
        value = (await self.api.get_setting(ctx, key)).strip().lower()
        return value if value in allowed else default

    async def _photo_result(self, ctx: Context, photo: dict[str, Any], group: str = "", group_score: float = 0) -> Result:
        photo_id = str(photo.get("id", ""))
        urls = self._dict(photo.get("urls"))
        links = self._dict(photo.get("links"))
        user = self._dict(photo.get("user"))
        user_links = self._dict(user.get("links"))
        photographer = str(user.get("name") or await self._translation(ctx, "result_photo_default_photographer"))
        description = str(
            photo.get("description") or photo.get("alt_description") or await self._translation(ctx, "result_photo_default_title")
        )
        thumb_url = str(urls.get("thumb") or urls.get("regular") or urls.get("full") or "")
        regular_url = str(urls.get("regular") or urls.get("full") or thumb_url)
        full_url = str(urls.get("full") or regular_url)
        photo_url = str(links.get("html") or "")
        download_location = str(links.get("download_location") or "")
        subtitle = await self._translation(ctx, "result_photo_attribution", photographer=photographer)

        return Result(
            id=photo_id,
            title=description,
            sub_title=subtitle,
            icon=WoxImage.new_url(thumb_url) if thumb_url else WoxImage.new_relative("images/app.png"),
            preview=WoxPreview(preview_type=WoxPreviewType.IMAGE, preview_data=f"url:{regular_url}"),
            score=100,
            group=group,
            group_score=group_score,
            actions=[
                ResultAction(
                    name="i18n:action_set_wallpaper",
                    icon=WoxImage.new_relative("images/app.png"),
                    is_default=True,
                    prevent_hide_after_action=True,
                    action=self.set_wallpaper,
                    context_data={
                        "id": photo_id,
                        "full_url": full_url,
                        "download_location": download_location,
                    },
                ),
                ResultAction(
                    name="i18n:action_open_unsplash",
                    icon=WoxImage.new_theme("open"),
                    action=self.open_unsplash,
                    context_data={
                        "photo_url": photo_url,
                        "photographer_url": str(user_links.get("html") or ""),
                    },
                ),
            ],
        )

    async def set_wallpaper(self, ctx: Context, action_context: ActionContext) -> None:
        access_key = (await self.api.get_setting(ctx, "access_key")).strip()
        full_url = action_context.context_data.get("full_url", "")
        download_location = action_context.context_data.get("download_location", "")
        photo_id = action_context.context_data.get("id", "unsplash")
        if not access_key or not full_url:
            await self.api.notify(ctx, await self._translation(ctx, "notify_photo_data_incomplete"))
            return

        directory = await self._download_dir(ctx)
        filename = f"{photo_id}.jpg"
        toolbar_msg_id = f"unsplash-wallpaper-{photo_id or 'current'}"
        try:
            await self._show_wallpaper_status(
                ctx,
                toolbar_msg_id,
                await self._translation(ctx, "toolbar_downloading_wallpaper"),
                progress=10,
                indeterminate=True,
            )
            await self.client.track_download(access_key, download_location)
            image_path = await self.downloader.download(access_key, full_url, directory, filename)
            await self._show_wallpaper_status(
                ctx,
                toolbar_msg_id,
                await self._translation(ctx, "toolbar_setting_wallpaper"),
                progress=75,
                indeterminate=True,
            )
            await asyncio.to_thread(self.wallpaper_setter, image_path)
        except Exception as error:
            message = await self._translation(ctx, "toolbar_failed_set_wallpaper", error=error)
            await self._show_wallpaper_status(ctx, toolbar_msg_id, message, progress=100)
            await self.api.notify(ctx, message)
            return

        await self.api.clear_toolbar_msg(ctx, toolbar_msg_id)
        await self.api.notify(ctx, await self._translation(ctx, "notify_wallpaper_updated"))

    async def _show_wallpaper_status(
        self,
        ctx: Context,
        toolbar_msg_id: str,
        title: str,
        *,
        progress: int,
        indeterminate: bool = False,
    ) -> None:
        await self.api.show_toolbar_msg(
            ctx,
            ToolbarMsg(
                id=toolbar_msg_id,
                title=title,
                icon=WoxImage.new_relative("images/app.png"),
                progress=progress,
                indeterminate=indeterminate,
            ),
        )

    async def open_unsplash(self, _ctx: Context, action_context: ActionContext) -> None:
        url = action_context.context_data.get("photo_url") or action_context.context_data.get("photographer_url")
        if url:
            await asyncio.to_thread(webbrowser.open, url)

    async def _download_dir(self, ctx: Context) -> Path:
        raw = (await self.api.get_setting(ctx, "download_dir")).strip()
        return Path(raw).expanduser() if raw else default_download_dir()

    async def _access_key_result(self, ctx: Context) -> Result:
        preview = await self._translation(ctx, "result_access_key_preview")
        return Result(
            title="i18n:result_access_key_title",
            sub_title="i18n:result_access_key_subtitle",
            icon=WoxImage.new_relative("images/app.png"),
            preview=WoxPreview(preview_type=WoxPreviewType.MARKDOWN, preview_data=preview),
            score=100,
        )

    async def _api_error_result(self, ctx: Context, error: UnsplashAPIError) -> Result:
        messages = {
            401: "api_error_invalid_key",
            403: "api_error_forbidden",
            429: "api_error_rate_limited",
        }
        message_key = messages.get(error.status_code, "api_error_generic")
        message = await self._translation(ctx, message_key, error=error)
        return self._message_result(await self._translation(ctx, "api_error_title"), message)

    async def _localized_message_result(self, ctx: Context, title_key: str, subtitle_key: str, **values: Any) -> Result:
        return self._message_result(
            await self._translation(ctx, title_key, **values),
            await self._translation(ctx, subtitle_key, **values),
        )

    def _message_result(self, title: str, subtitle: str) -> Result:
        return Result(
            title=title,
            sub_title=subtitle,
            icon=WoxImage.new_relative("images/app.png"),
            preview=WoxPreview(preview_type=WoxPreviewType.MARKDOWN, preview_data=subtitle),
            score=100,
        )

    async def _translation(self, ctx: Context, key: str, **values: Any) -> str:
        value = await self.api.get_translation(ctx, key)
        if value and value != key:
            return value.format(**values) if values else value
        return key.format(**values) if values else key

    def _dict(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}


MyPlugin = UnsplashPlugin
plugin = UnsplashPlugin()
