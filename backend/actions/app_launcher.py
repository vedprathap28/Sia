# Sia AI - App Launcher (Windows)
# All AppIDs verified from Get-StartApps on Sonu's machine (18-Apr-2026)
#
# Launch priority:
# 1. Direct EXE path  (Brave, Chrome, VSCode, Python)
# 2. Store AppID via shell:AppsFolder  (WhatsApp, Notepad, Calculator etc)
# 3. Shell / built-in commands  (cmd, explorer, taskmgr etc)
# 4. Start Menu .lnk search
# 5. PATH fallback
from __future__ import annotations
import os
import subprocess
from pathlib import Path
from backend.core.logger import get_logger

log = get_logger("app_launcher")

# ── Confirmed EXE paths ────────────────────────────────────────────────────────
_EXE_PATHS: dict[str, str] = {
    "brave":              r"C:\Users\sonuc\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe",
    "brave browser":      r"C:\Users\sonuc\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe",
    "chrome":             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "google chrome":      r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "vscode":             r"C:\Users\sonuc\AppData\Local\Programs\Microsoft VS Code\Code.exe",
    "visual studio code": r"C:\Users\sonuc\AppData\Local\Programs\Microsoft VS Code\Code.exe",
    "code":               r"C:\Users\sonuc\AppData\Local\Programs\Microsoft VS Code\Code.exe",
    "python":             r"C:\Users\sonuc\AppData\Local\Python\pythoncore-3.14-64\python.exe",
}

# ── Verified AppIDs from Get-StartApps ────────────────────────────────────────
_APP_IDS: dict[str, str] = {
    # Communication
    "whatsapp":                 "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
    "teams":                    "MSTeams_8wekyb3d8bbwe!MSTeams",
    "microsoft teams":          "MSTeams_8wekyb3d8bbwe!MSTeams",
    "teams personal":           "MicrosoftTeams_8wekyb3d8bbwe!MicrosoftTeams",
    "outlook":                  "Microsoft.OutlookForWindows_8wekyb3d8bbwe!Microsoft.OutlookforWindows",
    "outlook classic":          "Microsoft.Office.OUTLOOK.EXE.15",
    "mail":                     "microsoft.windowscommunicationsapps_8wekyb3d8bbwe!microsoft.windowslive.mail",
    "calendar":                 "microsoft.windowscommunicationsapps_8wekyb3d8bbwe!microsoft.windowslive.calendar",
    "phone link":               "Microsoft.YourPhone_8wekyb3d8bbwe!App",

    # Microsoft Office
    "word":                     "Microsoft.Office.WINWORD.EXE.15",
    "excel":                    "Microsoft.Office.EXCEL.EXE.15",
    "powerpoint":               "Microsoft.Office.POWERPNT.EXE.15",
    "onenote":                  "Microsoft.Office.ONENOTE.EXE.15",
    "access":                   "Microsoft.Office.MSACCESS.EXE.15",
    "publisher":                "Microsoft.Office.MSPUB.EXE.15",

    # Windows built-in apps
    "notepad":                  "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App",
    "calculator":               "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
    "paint":                    "Microsoft.Paint_8wekyb3d8bbwe!App",
    "camera":                   "Microsoft.WindowsCamera_8wekyb3d8bbwe!App",
    "clock":                    "Microsoft.WindowsAlarms_8wekyb3d8bbwe!App",
    "photos":                   "Microsoft.Windows.Photos_8wekyb3d8bbwe!App",
    "snipping tool":            "Microsoft.ScreenSketch_8wekyb3d8bbwe!App",
    "snip":                     "Microsoft.ScreenSketch_8wekyb3d8bbwe!App",
    "sticky notes":             "Microsoft.MicrosoftStickyNotes_8wekyb3d8bbwe!App",
    "to do":                    "Microsoft.Todos_8wekyb3d8bbwe!App",
    "todo":                     "Microsoft.Todos_8wekyb3d8bbwe!App",
    "microsoft to do":          "Microsoft.Todos_8wekyb3d8bbwe!App",
    "maps":                     "Microsoft.WindowsMaps_8wekyb3d8bbwe!App",
    "weather":                  "Microsoft.BingWeather_8wekyb3d8bbwe!App",
    "news":                     "Microsoft.BingNews_8wekyb3d8bbwe!AppexNews",
    "sound recorder":           "Microsoft.WindowsSoundRecorder_8wekyb3d8bbwe!App",
    "media player":             "Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic",
    "films":                    "Microsoft.ZuneVideo_8wekyb3d8bbwe!Microsoft.ZuneVideo",
    "tv":                       "Microsoft.ZuneVideo_8wekyb3d8bbwe!Microsoft.ZuneVideo",
    "store":                    "Microsoft.WindowsStore_8wekyb3d8bbwe!App",
    "microsoft store":          "Microsoft.WindowsStore_8wekyb3d8bbwe!App",
    "terminal":                 "Microsoft.WindowsTerminal_8wekyb3d8bbwe!App",
    "xbox":                     "Microsoft.GamingApp_8wekyb3d8bbwe!Microsoft.Xbox.App",
    "game bar":                 "Microsoft.XboxGamingOverlay_8wekyb3d8bbwe!App",
    "feedback hub":             "Microsoft.WindowsFeedbackHub_8wekyb3d8bbwe!App",
    "copilot":                  "Microsoft.Copilot_8wekyb3d8bbwe!App",
    "whiteboard":               "Microsoft.Whiteboard_8wekyb3d8bbwe!Whiteboard",
    "clipchamp":                "Clipchamp.Clipchamp_yxz26nhyzhsrt!App",
    "power automate":           "Microsoft.PowerAutomateDesktop_8wekyb3d8bbwe!PAD.Console",
    "journal":                  "Microsoft.MicrosoftJournal_8wekyb3d8bbwe!App",
    "windows security":         "Microsoft.SecHealthUI_8wekyb3d8bbwe!SecHealthUI",
    "microsoft defender":       "Microsoft.6365217CE6EB4_8wekyb3d8bbwe!App",

    # HP apps
    "hp smart":                 "AD2F1837.HPPrinterControl_v10z8vjag6ke6!AD2F1837.HPPrinterControl",
    "omen":                     "AD2F1837.OMENCommandCenter_v10z8vjag6ke6!AD2F1837.OMENCommandCenter",
    "omen gaming hub":          "AD2F1837.OMENCommandCenter_v10z8vjag6ke6!AD2F1837.OMENCommandCenter",
    "hp support assistant":     "AD2F1837.HPSupportAssistant_v10z8vjag6ke6!AD2F1837.HPSupportAssistant",
    "hp":                       "AD2F1837.myHP_v10z8vjag6ke6!App",
    "realtek":                  "RealtekSemiconductorCorp.RealtekAudioControl_dt26b99r8h8gj!App",
    "realtek audio":            "RealtekSemiconductorCorp.RealtekAudioControl_dt26b99r8h8gj!App",

    # Third-party
    "brave":                    "Brave.HE34DRVLFGO5R2WVNM7OQTBZ4A",
    "apple tv":                 "AppleInc.AppleTVWin_nzyj5cx40ttqa!App",
    "qtorrent":                 "59052ChromeLisa.qTorrent_y420hpqxfn4g8!App",
    "torrent":                  "59052ChromeLisa.qTorrent_y420hpqxfn4g8!App",
    "expressvpn":               "ExpressVpn.ExpressVpnNotificationService",
    "express vpn":              "ExpressVpn.ExpressVpnNotificationService",
    "alexa":                    "57540AMZNMobileLLC.AmazonAlexa_22t9g3sebte08!App",
    "jio saavn":                "Saavn.SaavnMusicRadio_ax02tpqam95xr!App",
    "saavn":                    "Saavn.SaavnMusicRadio_ax02tpqam95xr!App",
    "claude":                   "Claude_pzs8sxrjxfjjc!Claude",
    "onedrive":                 "Microsoft.SkyDrive.Desktop",
    "solitaire":                "Microsoft.MicrosoftSolitaireCollection_8wekyb3d8bbwe!App",
    "quick assist":             "MicrosoftCorporationII.QuickAssist_8wekyb3d8bbwe!App",
    "mixed reality":            "Microsoft.MixedReality.Portal_8wekyb3d8bbwe!App",
}

# ── Shell / built-in commands ──────────────────────────────────────────────────
_SHELL_CMDS: dict[str, list[str]] = {
    "cmd":                  ["cmd"],
    "command prompt":       ["cmd"],
    "powershell":           ["powershell"],
    "explorer":             ["explorer"],
    "file explorer":        ["explorer"],
    "task manager":         ["taskmgr"],
    "taskmgr":              ["taskmgr"],
    "control panel":        ["control"],
    "regedit":              ["regedit"],
    "registry editor":      ["regedit"],
    "msconfig":             ["msconfig"],
    "system configuration": ["msconfig"],
    "notepad++":            ["notepad++"],
    "edge":                 ["msedge"],
    "microsoft edge":       ["msedge"],
    "wsl":                  ["wsl"],
    "settings":             ["explorer", "ms-settings:"],
    "disk cleanup":         ["cleanmgr"],
}


def _launch_via_exe(path: str) -> bool:
    try:
        p = Path(path)
        if p.exists():
            subprocess.Popen([str(p)], creationflags=subprocess.DETACHED_PROCESS)
            log.info("Launched via EXE: %s", path)
            return True
        log.warning("EXE not found: %s", path)
        return False
    except Exception as e:
        log.warning("EXE launch failed: %s -> %s", path, e)
        return False


def _launch_via_appid(app_id: str) -> bool:
    """
    Launch using shell:AppsFolder via explorer.exe.
    This is the most reliable method for Store/modern apps.
    """
    try:
        subprocess.Popen(
            ["explorer.exe", f"shell:AppsFolder\\{app_id}"],
            creationflags=subprocess.DETACHED_PROCESS,
        )
        log.info("Launched via AppID: %s", app_id)
        return True
    except Exception as e:
        log.warning("AppID launch failed: %s -> %s", app_id, e)
        return False


def _launch_via_shell(cmd_parts: list[str]) -> bool:
    try:
        subprocess.Popen(cmd_parts, shell=False)
        log.info("Launched via shell: %s", cmd_parts)
        return True
    except Exception as e:
        log.warning("Shell launch failed: %s -> %s", cmd_parts, e)
        return False


def _launch_via_startmenu(name: str) -> bool:
    search_dirs = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path("C:/ProgramData/Microsoft/Windows/Start Menu/Programs"),
    ]
    name_lower = name.lower()
    for d in search_dirs:
        if not d.exists():
            continue
        for lnk in d.rglob("*.lnk"):
            if name_lower in lnk.stem.lower():
                try:
                    os.startfile(str(lnk))
                    log.info("Launched via Start Menu: %s", lnk)
                    return True
                except Exception:
                    continue
    return False


def launch_app(app_name: str) -> str:
    name = app_name.strip().lower()
    log.info("Launching: '%s'", name)

    # 1. Direct EXE (fastest, most reliable)
    if name in _EXE_PATHS:
        if _launch_via_exe(_EXE_PATHS[name]):
            return f"Launched {app_name}"

    # 2. Store AppID
    if name in _APP_IDS:
        if _launch_via_appid(_APP_IDS[name]):
            return f"Launched {app_name}"

    # 3. Shell command
    if name in _SHELL_CMDS:
        if _launch_via_shell(_SHELL_CMDS[name]):
            return f"Launched {app_name}"

    # 4. Start Menu search
    if _launch_via_startmenu(name):
        return f"Launched {app_name}"

    # 5. PATH fallback
    try:
        subprocess.Popen([app_name], shell=True)
        log.info("PATH fallback: %s", app_name)
        return f"Launched {app_name}"
    except Exception:
        return f"Could not find '{app_name}'. Make sure it's installed."