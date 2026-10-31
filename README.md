# SizzleMaker

SizzleMaker is a way to create incredible sizzle videos with your agent. All you need to do is give it a public Drive link with your video files and it will extract them and use them. It can put overlays on top. It can cut to music and beats if you give it your own MP3 or WAV file. You can also add voice overs. Its a great way to craft a sizzle video. Ideal to use with Muse, but it also works with Claude or ChatGPT agents.

CLI only. Built for agents. No editor, no timeline, no clicking around.

## What you need

- Python 3.10 or newer
- ffmpeg and ffprobe on your PATH
- Your own clips and your own music. Nothing is uploaded anywhere. Everything runs on your machine.
- For Drive links: `pip install "sizzlemaker[drive]"` pulls in gdown. Or skip Drive and point it at a local folder.

## Install

```
pip install .
```

## Use it

Plan first. This reads your music, finds the tempo, builds the beat grid and writes a cut plan as JSON. Nothing renders yet.

```
sizzlemaker plan --input-dir ./clips --music track.mp3 --beats-per-cut 4
```

Or from a public Drive folder link that you own:

```
sizzlemaker plan --drive-link "https://drive.google.com/drive/folders/YOUR_FOLDER" --music track.mp3
```

Look at the shots before you cut. Contact sheets, one per clip:

```
sizzlemaker sheets --input-dir ./clips
```

Build the sizzle:

```
sizzlemaker build --input-dir ./clips --music track.mp3 --output sizzle.mp4
```

Add the extras when you want them:

- `--title "Your Title"` burns a title in at the bottom
- `--overlay-image logo.png` centers a PNG overlay on top
- `--voiceover vo.mp3` mixes a voiceover over the music, music drops under it
- `--vertical` renders 1080x1920 instead of 1920x1080
- `--start 32 --duration 30` cuts one window of the track instead of the whole thing

Check the render like an adult:

```
sizzlemaker audit sizzle.mp4
```

The audit uses ffprobe and confirms the file has video and audio and runs the length the plan called for. Build runs the same audit on its own output and fails loud if the render is off.

## How it cuts

- The tempo is detected from the music itself, and cuts land on the beat grid, every N beats. Default is 4.
- Segments spread across all of your clips in order, walking forward through each one, so the same shot does not repeat back to back.
- Full frames only. Clips are scaled to fit the canvas and padded. Nothing gets cropped or zoom cut.
- The whole plan is saved as plan.json in the work folder, so you or your agent can read exactly what got cut where, change it, and rebuild.

## Output

- The finished video at the path you gave with `--output`
- `sizzle_work/plan.json` with the tempo and every segment: source clip, in point, out point, and where it lands on the music

## License

MIT. See LICENSE.
