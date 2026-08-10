# Preview Streams and Channels in the Browser

Before you assign a stream to a channel, or after you've built one, you can
play it back right in ECM without opening an external player. This article
covers the preview modal itself: what it plays, the three playback modes, and
the alternative actions inside it. For the row-level buttons that open it, see
[Streams](streams-overview.md#inspect-a-single-stream).

## Common tasks

### Preview a single stream before you assign it

1. On an assigned stream row, or a row in the Streams panel, click **Preview
   stream in browser** (the `visibility` icon).
2. The preview modal opens with an embedded video player and the stream's
   metadata: name, TVG-ID, group, and M3U provider.

**Result:** You can confirm a stream is actually alive, at the quality you
expect, before spending time building a channel around it. The adjacent
**Open in VLC** button (`play_circle`) launches VLC instead of playing in the
browser; it does not open this modal.

### Preview a channel's actual output

1. Open the channel's **Channel actions** menu and select **Preview**.

**Result:** Unlike a raw stream preview, this tests the real Dispatcharr proxy
output for that channel, end to end, the same URL a media client would
connect to. Use this once a channel has streams assigned, when you want to
confirm the whole path works, not just the source stream.

### Choose which playback mode the preview uses

The preview modal shows a mode indicator naming the current playback mode.
That mode comes from **Settings → Appearance → Browser Playback Mode** (see
[Appearance](../settings/appearance.md#choose-how-open-in-vlc-and-browser-stream-preview-behave)):

| Mode | Behavior |
|-|-|
| **Passthrough** | Direct proxy, fastest, but can fail on AC-3/E-AC-3/DTS audio the browser can't decode. |
| **Transcode** | FFmpeg transcodes audio to AAC for browser compatibility. Recommended default; costs backend CPU. |
| **Video Only** | Strips audio entirely, for a quick silent check that the video itself is alive. |

**Result:** Changing the setting takes effect on the next preview you open;
it does not affect a preview already playing.

### Use the modal's other actions

From the preview modal you can also:

- **Open in VLC**: launch the same stream in VLC media player instead of the
  in-browser player.
- **Download M3U**: download a one-line M3U playlist file pointing at the
  stream, useful for handing off to another player.
- **Copy URL**: copy the direct stream URL to your clipboard.

> Stream URLs usually carry your provider's credentials as query parameters.
> Treat anything you copy here as a secret.

## Going deeper

- [Streams](streams-overview.md): reading and filtering the Streams panel
  where a stream-level preview starts.
- [Channel Manager](channels-overview.md): the Channel actions menu a
  channel-level preview starts from.
- [Appearance](../settings/appearance.md#choose-how-open-in-vlc-and-browser-stream-preview-behave): the Browser Playback Mode and Open in VLC Behavior settings, plus the platform setup scripts for the `vlc://` protocol handler.
