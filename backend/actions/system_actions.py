# Sia AI - Central Action Dispatcher (optimized — module-level imports)
from __future__ import annotations
from typing import Any
from backend.core.logger import get_logger

log = get_logger("actions")

# ── All imports moved to module level — loaded ONCE at server startup ────────
from backend.actions.browser_actions import (
    open_url, search_web, youtube_play, search_in_site,
    close_tab_by_name, what_is_playing,
    youtube_pause_resume, youtube_seek_forward, youtube_seek_back,
    youtube_fullscreen, youtube_volume_up, youtube_volume_down,
    chrome_new_tab, chrome_close_tab, chrome_next_tab, chrome_prev_tab,
    chrome_refresh, chrome_back, chrome_forward, chrome_zoom_in,
    chrome_zoom_out, chrome_zoom_reset, chrome_incognito,
    chrome_history, chrome_downloads, chrome_open_profile,
)
from backend.actions.app_launcher import launch_app
from backend.actions.volume_actions import (
    volume_up, volume_down, volume_mute, volume_unmute, volume_set,
    media_play_pause, media_next, media_prev, media_stop,
    brightness_up, brightness_down, brightness_set,
    night_light_on, night_light_off,
)
from backend.actions.window_actions import (
    window_minimise, window_maximise, window_close,
    window_snap_left, window_snap_right, show_desktop, open_task_view,
    virtual_desktop_new, virtual_desktop_switch, window_switch,
)
from backend.actions.system_ops import (
    create_file, read_file, delete_path, move_path, copy_path,
    rename_path, open_folder, create_folder, search_file, list_files,
    take_screenshot, system_shutdown, system_restart,
    system_sleep, system_lock, cancel_shutdown, empty_recycle_bin,
    check_battery, check_system, open_task_manager,
    type_text, press_key, clipboard_copy, clipboard_paste,
    change_wallpaper, toggle_dark_mode, open_emoji_picker,
    open_clipboard_history, set_reminder, set_timer,
)
from backend.actions.ai_actions import (
    summarise_clipboard, fix_spelling_clipboard, translate_clipboard,
)
from backend.actions.whatsapp_actions import (
    whatsapp_send, whatsapp_read, whatsapp_reply,
)
from backend.actions.utility_actions import (
    read_clipboard, save_clipboard_to_file, google_selected_text,
    list_open_apps, close_all_except, type_signature,
    what_time_in, coin_flip, roll_dice, tell_joke,
)
from backend.actions.rag_actions import (
    ingest_file, ingest_folder, search_documents,
    list_documents, delete_document,
)


async def execute_action(action: str, params: dict[str, Any]) -> str:
    log.info("▶ %s  %s", action, params)
    try:
        match action:
            # Browser
            case "open_browser":
                return open_url(params.get("url", "https://google.com"), params.get("profile", "default"))
            case "search_web":
                return search_web(params.get("query", ""), params.get("engine", "google"))
            case "search_in_site":
                return search_in_site(params.get("url", ""), params.get("query", ""))
            case "youtube_play":
                return youtube_play(params.get("query", ""))

            case "open_app":
                return launch_app(params.get("app_name", ""))

            case "chrome_new_tab":       return chrome_new_tab()
            case "chrome_close_tab":     return chrome_close_tab()
            case "chrome_next_tab":      return chrome_next_tab()
            case "chrome_prev_tab":      return chrome_prev_tab()
            case "chrome_refresh":       return chrome_refresh()
            case "chrome_back":          return chrome_back()
            case "chrome_forward":       return chrome_forward()
            case "chrome_zoom_in":       return chrome_zoom_in()
            case "chrome_zoom_out":      return chrome_zoom_out()
            case "chrome_zoom_reset":    return chrome_zoom_reset()
            case "chrome_incognito":     return chrome_incognito()
            case "chrome_history":       return chrome_history()
            case "chrome_downloads":     return chrome_downloads()
            case "chrome_profile":       return chrome_open_profile(params.get("profile", "sonu"))

            case "volume_up":            return volume_up(int(params.get("amount", 10)))
            case "volume_down":          return volume_down(int(params.get("amount", 10)))
            case "volume_mute":          return volume_mute()
            case "volume_unmute":        return volume_unmute()
            case "volume_set":           return volume_set(int(params.get("level", 50)))

            case "media_play_pause":     return media_play_pause()
            case "media_next":           return media_next()
            case "media_prev":           return media_prev()
            case "media_stop":           return media_stop()
            case "brightness_up":        return brightness_up(int(params.get("amount", 10)))
            case "brightness_down":      return brightness_down(int(params.get("amount", 10)))
            case "brightness_set":       return brightness_set(int(params.get("level", 70)))
            case "night_light_on":       return night_light_on()
            case "night_light_off":      return night_light_off()

            case "window_minimise":      return window_minimise()
            case "window_maximise":      return window_maximise()
            case "window_close":         return window_close()
            case "window_snap_left":     return window_snap_left()
            case "window_snap_right":    return window_snap_right()
            case "show_desktop":         return show_desktop()
            case "open_task_view":       return open_task_view()
            case "virtual_desktop_new":  return virtual_desktop_new()
            case "virtual_desktop_switch": return virtual_desktop_switch(int(params.get("number", 2)))
            case "window_switch":        return window_switch(params.get("app_name", ""))

            case "take_screenshot":      return take_screenshot()
            case "system_shutdown":      return system_shutdown()
            case "system_restart":       return system_restart()
            case "system_sleep":         return system_sleep()
            case "system_lock":          return system_lock()
            case "cancel_shutdown":      return cancel_shutdown()
            case "empty_recycle_bin":    return empty_recycle_bin()
            case "check_battery":        return check_battery()
            case "check_system":         return check_system()
            case "open_task_manager":    return open_task_manager()

            case "create_file":   return create_file(params.get("path",""), params.get("content",""))
            case "read_file":     return read_file(params.get("path",""))
            case "delete_file":   return delete_path(params.get("path",""))
            case "move_file":     return move_path(params.get("src",""), params.get("dst",""))
            case "copy_file":     return copy_path(params.get("src",""), params.get("dst",""))
            case "rename_file":   return rename_path(params.get("src",""), params.get("dst",""))
            case "search_file":   return search_file(params.get("name",""), params.get("location","~"))
            case "list_files":    return list_files(params.get("path","~"))
            case "open_folder":   return open_folder(params.get("path",""))
            case "create_folder": return create_folder(params.get("path",""))
            case "delete_folder": return delete_path(params.get("path",""))
            case "move_folder":   return move_path(params.get("src",""), params.get("dst",""))
            case "copy_folder":   return copy_path(params.get("src",""), params.get("dst",""))

            case "type_text":            return type_text(params.get("text", ""))
            case "press_key":            return press_key(params.get("key", ""))
            case "clipboard_copy":       return clipboard_copy(params.get("text", ""))
            case "clipboard_paste":      return clipboard_paste()
            case "open_emoji_picker":    return open_emoji_picker()
            case "clipboard_history":    return open_clipboard_history()

            case "change_wallpaper":     return change_wallpaper(params.get("path", ""))
            case "toggle_dark_mode":     return toggle_dark_mode(params.get("mode", "dark"))

            case "set_reminder":         return set_reminder(params.get("message", ""), int(params.get("minutes", 5)))
            case "set_timer":            return set_timer(int(params.get("minutes", 5)))

            case "ai_summarise_clipboard":  return await summarise_clipboard()
            case "ai_fix_spelling":         return await fix_spelling_clipboard()
            case "ai_translate_clipboard":  return await translate_clipboard(params.get("language", "Hindi"))

            case "close_tab_by_name":    return close_tab_by_name(params.get("name", ""))
            case "what_is_playing":      return what_is_playing()

            case "youtube_pause_resume":    return youtube_pause_resume()
            case "youtube_seek_forward":    return youtube_seek_forward(int(params.get("seconds", 10)))
            case "youtube_seek_back":       return youtube_seek_back(int(params.get("seconds", 10)))
            case "youtube_fullscreen":      return youtube_fullscreen()
            case "youtube_volume_up":       return youtube_volume_up(int(params.get("amount", 10)))
            case "youtube_volume_down":     return youtube_volume_down(int(params.get("amount", 10)))

            case "whatsapp_send":        return whatsapp_send(params.get("contact", ""), params.get("message", ""))
            case "whatsapp_read":        return whatsapp_read(params.get("contact", ""), int(params.get("n", 5)))
            case "whatsapp_reply":       return whatsapp_reply(params.get("contact", ""), params.get("message", ""))

            case "read_clipboard":           return read_clipboard()
            case "save_clipboard_to_file":   return save_clipboard_to_file()
            case "google_selected_text":     return google_selected_text()
            case "list_open_apps":           return list_open_apps()
            case "close_all_except":         return close_all_except(params.get("keep", ""))
            case "type_signature":           return type_signature()
            case "what_time_in":             return what_time_in(params.get("city", "India"))
            case "coin_flip":                return coin_flip()
            case "roll_dice":                return roll_dice(int(params.get("sides", 6)))
            case "tell_joke":                return await tell_joke()

            case "ingest_file":        return ingest_file(params.get("path", ""))
            case "ingest_folder":      return ingest_folder(params.get("path", "~/Desktop"))
            case "search_documents":   return search_documents(params.get("query", ""))
            case "list_documents":     return list_documents()
            case "delete_document":    return delete_document(params.get("name", ""))

            case _:
                log.warning("Unknown action: '%s'", action)
                return f"Unknown action: '{action}'"

    except Exception as e:
        log.exception("Action '%s' failed: %s", action, e)
        return f"Action '{action}' failed: {e}"