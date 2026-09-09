# Production Checklist

## Code

- [ ] Production branch checked out
- [ ] No secrets in Git diff
- [ ] Compile checks pass
- [ ] Full unit test suite passes

## Installer

- [ ] `sudo bash bootstrap.sh` succeeds on a fresh Ubuntu 24.04 GPU VM
- [ ] No manual dependency repair required
- [ ] Z-Image isolated Torch sees CUDA
- [ ] Main app Torch remains intact
- [ ] Remotion installed
- [ ] Real Remotion render succeeds
- [ ] systemd service active

## APIs

- [ ] Gemini
- [ ] Together AI
- [ ] Pexels
- [ ] Pixabay
- [ ] Coverr
- [ ] Openverse
- [ ] Google OAuth for YouTube
- [ ] Zernio if Facebook is required

## Google / YouTube

- [ ] YouTube Data API v3 enabled
- [ ] Branding configured
- [ ] Audience configured
- [ ] Data Access reviewed
- [ ] Web OAuth client created
- [ ] Callback exact
- [ ] Channel connects
- [ ] Test publish succeeds

## Fresh-data research

- [ ] Current-topic test shows `LIVE_RESEARCH|enabled`
- [ ] Evergreen test shows `LIVE_RESEARCH|skip|evergreen`
- [ ] Grounding returns sources
- [ ] Grounding failure safely falls back

## Presets

- [ ] Save
- [ ] Load
- [ ] Delete
- [ ] Server JSON sync
- [ ] title/script excluded

## Backup / restore

- [ ] Archive created
- [ ] Checksum passes
- [ ] DB included
- [ ] `.env` included
- [ ] Gemini state included
- [ ] media history included
- [ ] presets included
- [ ] restore tested on fresh VM

## Real video tests

- [ ] 10-minute evergreen video
- [ ] 15-minute current/live-research video
- [ ] script quality reviewed
- [ ] word count/duration reviewed
- [ ] TTS timing reviewed
- [ ] caption timing/style reviewed
- [ ] media relevance reviewed
- [ ] Z-Image reviewed
- [ ] render speed reviewed
- [ ] final MP4 validated

## Final scale test

- [ ] 30-minute video completed
- [ ] all scenes valid
- [ ] final duration acceptable
- [ ] total generation time recorded
- [ ] retries recorded
- [ ] no critical bottleneck

## Launch

- [ ] Final production backup
- [ ] Git status reviewed
- [ ] Final checkpoint committed
- [ ] Production branch pushed
- [ ] HTTPS/domain confirmed
- [ ] 5 channel niches finalized
- [ ] Publishing accounts connected
