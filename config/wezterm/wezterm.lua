-- wezterm.lua - WezTerm Terminal Emulator Configuration

local wezterm = require 'wezterm'
local act = wezterm.action

local config = wezterm.config_builder()

local mod = {}
local is_macos = wezterm.target_triple:find('apple') ~= nil
mod.SUPER = is_macos and 'SUPER' or 'CTRL|SHIFT'
mod.SUPER_REV = is_macos and 'SUPER|CTRL' or 'CTRL|SHIFT|ALT'

-- Initial window size; F12 toggles fullscreen manually.
config.initial_cols = 80
config.initial_rows = 24

-- Font settings
if is_macos then
  -- Homebrew installs fonts into ~/Library/Fonts. CoreText may not discover
  -- them immediately (including after an SSH install); also scan the files.
  config.font_dirs = { wezterm.home_dir .. '/Library/Fonts', '/Library/Fonts' }
end
config.font = wezterm.font_with_fallback {
  -- 'Protomolecule',
  'MesloLGS NF',
  'Songti SC',
}
config.font_size = 16.0

config.window_frame = {
  font_size = 14.0,  -- tab栏和标题栏字体大小
}

config.enable_scroll_bar = true

-- Keep glyphs opaque while allowing the desktop through terminal backgrounds.
config.window_background_opacity = 0.80
config.text_background_opacity = 0.80
if is_macos then config.macos_window_background_blur = 100 end

if is_macos then
  wezterm.on('gui-startup', function(cmd)
    local screen = wezterm.gui.screens().active
    cmd = cmd or {}
    cmd.position = cmd.position or {
      x = screen.width / 2,
      y = screen.height / 2,
      origin = 'ActiveScreen',
    }
    wezterm.mux.spawn_window(cmd)
  end)
end

config.color_scheme = 'Monokai (base16)'
-- config.color_scheme = 'Monokai Pro' 
config.keys = {
  {
    key = 's',
    mods = mod.SUPER_REV,
    action = wezterm.action.QuickSelect,
  },
  {
    key = [[\]],
    mods = mod.SUPER,
    action = act.SplitVertical({ domain = 'CurrentPaneDomain' }),
   },
  {
    key = [[\]],
    mods = mod.SUPER_REV,
    action = act.SplitHorizontal({ domain = 'CurrentPaneDomain' }),
  },
  { key = 'Enter', mods = mod.SUPER_REV,     action = act.TogglePaneZoomState },
  { key = 'w',     mods = mod.SUPER,     action = act.CloseCurrentPane({ confirm = true }) },
  { key = 'k',     mods = mod.SUPER_REV, action = act.ActivatePaneDirection('Up') },
  { key = 'j',     mods = mod.SUPER_REV, action = act.ActivatePaneDirection('Down') },
  { key = 'h',     mods = mod.SUPER_REV, action = act.ActivatePaneDirection('Left') },
  { key = 'l',     mods = mod.SUPER_REV, action = act.ActivatePaneDirection('Right') },
  {
    key = 'p',
    mods = mod.SUPER_REV,
    action = act.PaneSelect({ alphabet = '1234567890', mode = 'SwapWithActiveKeepFocus' }),
  },
  {
    key = 'F12',
    -- mods = 'SHIFT|CTRL',
    action = wezterm.action.ToggleFullScreen,
  },
}

return config
