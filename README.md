# Chaplin

![Chaplin Thumbnail](./thumbnail.png)

A visual speech recognition (VSR) tool that reads your lips in real-time and types whatever you silently mouth. Runs fully locally.

Relies on a [model](https://github.com/mpc001/Visual_Speech_Recognition_for_Multiple_Languages?tab=readme-ov-file#autoavsr-models) trained on the [Lip Reading Sentences 3](https://mmai.io/datasets/lip_reading/) dataset as part of the [Auto-AVSR](https://github.com/mpc001/auto_avsr) project.

Watch a demo of Chaplin [here](https://youtu.be/qlHi0As2alQ).

## Setup

1. Clone the repository, and `cd` into it:
   ```sh
   git clone https://github.com/amanvirparhar/chaplin
   cd chaplin
   ```
2. Run the setup script...
   ```sh
   ./setup.sh
   ```
   ...which will automatically download the required model files from Hugging Face Hub and place them in the appropriate directories:
   ```
   chaplin/
   ├── benchmarks/
       ├── LRS3/
           ├── language_models/
               ├── lm_en_subword/
           ├── models/
               ├── LRS3_V_WER19.1/
   ├── ...
   ```
3. Choose the language cleanup model:
   - Local: install and run `ollama`, and pull the [`qwen3:4b`](https://ollama.com/library/qwen3:4b) model.
   - Cloud: set `CHAPLIN_LLM_PROVIDER=openai` and provide `OPENAI_API_KEY`.
4. Install [`uv`](https://github.com/astral-sh/uv).

## Usage

1. Run the following command:
   ```sh
   uv run --with-requirements requirements.txt --python 3.12 main.py config_filename=./configs/LRS3_V_WER19.1.ini detector=mediapipe
   ```
   To use a cloud OpenAI model instead of local Ollama cleanup:
   ```sh
   CHAPLIN_LLM_PROVIDER=openai \
   CHAPLIN_OPENAI_MODEL=gpt-5.4-nano \
   OPENAI_API_KEY=... \
   uv run --with-requirements requirements.txt --python 3.12 main.py config_filename=./configs/LRS3_V_WER19.1.ini detector=mediapipe
   ```
   Use `CHAPLIN_OPENAI_MODEL=gpt-5.4-mini` if you want stronger correction at higher cost/latency.
2. Once the camera feed is displayed, you can start "recording" by pressing the `option` key (Mac) or the `alt` key (Windows/Linux), and start mouthing words.
3. To stop recording, press the `option` key (Mac) or the `alt` key (Windows/Linux) again. The raw VSR output will get logged in your terminal, and the LLM-corrected version will be typed at your cursor.
4. To exit gracefully, focus on the window displaying the camera feed and press `q`.

## Language Trainer (web app)

An API-first web app that turns Chaplin into a daily spoken-language trainer for
English-as-a-second-language learners. One recording (video + audio) returns three lines:
the original of what you said, a refined version (a natural English translation if you
spoke Chinese, or a more-native rewrite if you spoke English), and the lip-read transcript.
It plays the refined line aloud (ElevenLabs), highlights the vocabulary upgrades, copies the
refined text to your clipboard, and saves each recording as a future fine-tuning pair.

### Run
```sh
./setup.sh                       # one-time: downloads VSR model weights
uv run --with-requirements requirements.txt --python 3.12 web_chaplin.py
# or, in an environment that already has the requirements installed:
python web_chaplin.py            # serves http://127.0.0.1:8765
```

### Use
1. Open http://127.0.0.1:8765 and click **Start Camera**.
2. In the right panel, set your **Gladia**, **OpenAI**, and **ElevenLabs** keys. They are
   stored locally in `~/.chaplin/keys.json` and reused every session — click **Change** to
   rotate any key. Env vars `GLADIA_API_KEY` / `OPENAI_API_KEY` / `ELEVENLABS_API_KEY`
   override the stored values (handy for deployment).
3. Press **Record**, speak (or silently mouth) to the camera, then press **Stop**.
   - Speaking Chinese → original in Chinese, refined = natural English translation.
   - Speaking English → original verbatim, refined = AI-optimized native version.
   - No voice (just mouthing) → the lip-read text becomes the input (English only).
4. The refined line is highlighted, copied to your clipboard, and spoken aloud.
5. Visit **Review** for your vocabulary upgrades (search/star) and session history.

Recordings are saved under `data/recordings/<id>/` (`clip.webm` + `audio.wav`) as
ground-truth pairs for future model fine-tuning. Run the test suite with `pytest`.

## Demo Studio

Record a product-demo screen take, edit the English lines, and export an MP4
with an ElevenLabs voice track and burned-in captions.

1. Open http://127.0.0.1:8765/demo and click **Start screen**.
2. Grant screen + microphone (camera is optional preview only).
3. **Record**, narrate in Chinese or English, **Stop**.
4. Edit the English column. **Generate voice & export** downloads
   `chaplin-demo-<id>.mp4` (screen + English voice + captions).
