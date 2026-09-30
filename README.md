# Unsplash

Search Unsplash photos from Wox and set the selected image as your desktop wallpaper.

# Install

Requires Wox 2.4.5 or later.

```
wpm install Unsplash
```

# Configure Unsplash Access Key

This plugin requires your own Unsplash Access Key.

1. Open https://unsplash.com/developers.
2. Sign in or create an Unsplash account.
3. Create a new application.
4. Open the application details page.
5. Copy the `Access Key`.
6. Open this plugin's settings in Wox and paste the key into `Unsplash Access Key`.

Keep the key private. Do not commit it to source control or share it in screenshots.

Unsplash apps start in Demo mode, which has a low hourly request limit. If searches return a rate limit message, wait for the limit to reset or follow Unsplash's production approval process.

# Usage

Type `unsplash` to show cached wallpapers from Unsplash's `wallpapers` topic. The first group shows the latest wallpapers, and the second group shows the most popular wallpapers. The plugin refreshes this cache in the background, so opening the default query does not call the Unsplash API directly.

Use `unsplash search mountain`, `unsplash search ocean`, or another search term to search directly. Plain text after the trigger, such as `unsplash ocean`, does not search; use the `search` command.

The `search` command offers an editable keyword hint. Search refinements let you change orientation (`Ctrl+O`, or `Cmd+O` on macOS) and content filtering (`Ctrl+H`, or `Cmd+H` on macOS) for the current query; plugin settings supply the defaults.

Other plugins and Wox AI Chat can call `search_photos` with `query` and optional `per_page`, `orientation`, and `content_filter`, then call `set_wallpaper` with a returned `photo_id` (the photo's `id`). Both tools require the configured Access Key. Searching returns Unsplash photo metadata; setting wallpaper downloads the photo, tracks the download, changes the wallpaper, and returns its local `path` without opening launcher UI.

Select a photo and run `Set as wallpaper`.

The plugin calls Unsplash download tracking before setting a wallpaper, as required by the Unsplash API guidelines.
