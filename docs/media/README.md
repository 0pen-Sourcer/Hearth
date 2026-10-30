# README film clips

Source: `media/film_cut7c_1280.mp4` on `0pen-Sourcer/hearth-dev`'s `shared` branch at
`abd1e37862eebc969c979f3f4dce085c079a4439`. Read with that branch's `media/STORYBOARD.md`,
`media/PLAN_cut7.md`, TASKS.md C45 and `notes/FINAL_HANDOFF_2026-10-29.md`.
The source is 1280 x 720, 60 fps, 91.65 seconds, without audio.

| GIF | Source start (s) | Source length (s) | Playback duration multiplier | Width | FPS | Palette colors | Bayer scale |
|---|---:|---:|---:|---:|---:|---:|---:|
| approval.gif | 17.65 | 2.00 | 2 | 1000 | 15 | 256 | 3 |
| brains.gif | 11.65 | 3.20 | 1.25 | 1000 | 15 | 256 | 3 |
| voice.gif | 37.20 | 6.00 | 1 | 1000 | 15 | 256 | 3 |
| team-room.gif | 19.80 | 4.20 | 1 | 960 | 12 | 128 | 4 |
| phone.gif | 65.65 | 3.85 | 1.1 | 1000 | 15 | 256 | 3 |
| looks.gif | 78.50 | 1.60 | 2.5 | 1000 | 15 | 256 | 3 |

Short shots play more slowly so their contents remain readable. All GIFs loop.
The smaller Team room palette keeps its moving scene below 8 MB. Frame timing is rounded to
GIF's hundredths of a second.

The phone clip shows Telegram, Discord and WhatsApp messaging. Cut 7's plan explicitly excludes
the phone app, so this source cannot provide C45's phone approval or call shot. That shot still
needs a separate capture. The README caption describes only the messaging shown here.

## Reproduce

Use the row's values in this PowerShell command, with the source film available locally:

```powershell
$start = 17.65
$length = 2
$multiplier = 2
$width = 1000
$fps = 15
$colors = 256
$bayer = 3
$name = 'approval'
$filter = "setpts=$multiplier*(PTS-STARTPTS),fps=$fps,scale=${width}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=${colors}:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=${bayer}:diff_mode=rectangle"
ffmpeg -y -ss $start -t $length -i media/film_cut7c_1280.mp4 -filter_complex $filter -an -loop 0 "docs/media/$name.gif"
```

## C45 handoff

Replaced the README hero and See it grid with six film GIFs and plain captions. Removed the seven
previous media assets after checking their frames and repository references. README prose outside
the visuals is unchanged. Verified GIF dimensions, duration, looping, size and complete decoding;
checked first, middle and last frames; rendered the README in Edge and checked its local image paths.
No application code changed. Phone approval/call footage remains unavailable in this source.
