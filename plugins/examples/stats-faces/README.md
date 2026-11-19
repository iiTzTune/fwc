# Stats faces

The display shows the start of the current epoch in UTC, network and channel
counts, and a score. The host supplies up to 16 network records. A record
contributes one point if its latest observation occurred in this epoch. Each
distinct channel after the first adds one point. A score of zero shows a
resting face, one to three shows a curious face, and four or more shows a busy
face. Repeated refreshes with the same counts keep the same face.

Set `plugins.stats-faces.settings.epoch_seconds` in `plugin-config.json` to an
integer from 30 to 3600. The default is 300. Epochs start on multiples of this
duration from the Unix epoch. The host samples every 15 seconds and needs
`networks.read` and `display.write`. Revoking either pauses the plugin and
preserves the last displayed frame.
