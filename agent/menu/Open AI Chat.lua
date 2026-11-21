-- Opens the Resolve AI Chat panel. The macOS app registers resolveaichat:// at install.
local url = "resolveaichat://open"
local opened = false
if bmd and bmd.openurl then opened = pcall(bmd.openurl, url) end
if not opened and os and os.execute then
  opened = pcall(os.execute, 'open "' .. url .. '"')
end
if opened then
  print("Opening Resolve AI Chat. Press Option-Command-A to show or hide it.")
else
  print("Open \"Resolve AI Chat\" from your Applications folder, then press Option-Command-A.")
end
