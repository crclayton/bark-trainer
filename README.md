# Bark Trainer

A program that teaches a dog to stay quiet. A microphone listens for barking, and the program rewards quiet with a treat from an automatic dispenser. It starts by asking for only a few seconds of quiet and slowly asks for longer stretches. If the dog barks, it starts over from short, easy intervals.

> **Status:** the training logic has been tested with a simulated dog. The microphone and dispenser parts have not yet been tested on real hardware, so expect to adjust settings at first.

---

## Contents

1. [How the training works](#how-the-training-works)
2. [What you need](#what-you-need)
3. [Setup, step by step](#setup-step-by-step)
4. [Running a training session](#running-a-training-session)
5. [Settings you can change](#settings-you-can-change)
6. [Reading the results](#reading-the-results)
7. [Safety and good practice](#safety-and-good-practice)
8. [Known limitations](#known-limitations)
9. [Troubleshooting](#troubleshooting)

---

## How the training works

Animal trainers call this method **DRO (differential reinforcement of other behaviour)**: the dog is rewarded for any stretch of time in which it does *not* bark.

Each session has two phases.

**1. Baseline (first 5 minutes).** The program only listens and gives no treats. It counts the barks and works out how long the dog usually goes between them. Example: 36 barks in 5 minutes means about one bark every 8 seconds.

**2. Training.** The program then follows these rules:

| What happens | What the program does |
|---|---|
| Start of training | Sets the first quiet interval to **half** the dog's usual gap between barks (about 4 seconds in the example). The dog gets a treat before it would normally bark, so it starts getting rewarded straight away. |
| The dog stays quiet for the whole interval | Gives a treat, then makes the next interval **20% longer** (4 s → 4.8 s → 5.8 s → 6.9 s …), up to a limit of 15 minutes. |
| The dog barks | Restarts the timer **and** drops the interval back to its starting length. |

Over time the dog learns that quiet earns treats, and the quiet periods get longer.

In the simulation, a dog that started out barking every 8 seconds was going several minutes between barks after about 30 minutes, and its longest rewarded quiet stretch reached 77 seconds. Real dogs vary a lot, so treat that only as a rough illustration.

---

## What you need

### Hardware

| Item | Notes |
|---|---|
| **A computer to run the program** | A **Raspberry Pi** (any model from 3 up) is the easiest choice because it can switch the dispenser on directly. A Linux laptop, Mac, or Windows PC also works if your dispenser can be triggered some other way (see below). |
| **A USB microphone** | Any cheap USB microphone or webcam microphone. Put it near where the dog usually barks. |
| **An automatic treat dispenser** | It needs to be something the computer can trigger. Two common options: <br>• **A dispenser with a button or motor, wired through a relay module** to a Raspberry Pi pin. This needs some basic wiring; a "5V relay module for Raspberry Pi" costs a few dollars. <br>• **A smart or Wi-Fi dispenser** that you can trigger with a command or web address. The program can run any command you give it. |
| **Small, soft treats** | Small enough that the dog can eat one in a second or two. |

> ⚠️ If you wire a relay, keep mains (wall-socket) electricity out of it unless you know what you're doing. Battery- or USB-powered dispensers are much safer to modify.

### Software

- **Python 3** (already installed on Raspberry Pi OS, most Linux, and macOS; on Windows, download it from [python.org](https://www.python.org/downloads/))
- Three add-on packages, installed in step 2 below

---

## Setup, step by step

All of these commands are typed into a **terminal** (also called the command line):
- **Raspberry Pi / Linux:** open the "Terminal" app.
- **Mac:** open "Terminal" from Applications → Utilities.
- **Windows:** open "Command Prompt" and type `python` instead of `python3` in the commands below.

### 1. Download this project

```
git clone https://github.com/crclayton/bark-trainer.git
cd bark-trainer
```

If you don't have `git`, click the green **Code** button on the GitHub page, choose **Download ZIP**, unzip it, and open a terminal in that folder.

### 2. Install the add-on packages

```
pip install -r requirements.txt
```

On Raspberry Pi OS you may get an "externally-managed-environment" error. If so, use this instead:

```
sudo apt install python3-numpy python3-gpiozero python3-pip portaudio19-dev
pip install sounddevice --break-system-packages
```

### 3. Try it with a pretend dog (no hardware needed)

```
python3 bark_trainer.py --simulate -v
```

This runs a full session in a few seconds against a simulated dog, so you can watch how treats and resets happen. Lines look like:

```
   4.4s  treat #1, next 3.6s
   8.1s  treat #2, next 4.3s
   9.2s  BARK  reset to 3.0s
```

### 4. Calibrate the microphone

The program decides "that was a bark" when the sound is louder than a **threshold**. Every room and microphone is different, so you need to find a good value:

```
python3 bark_trainer.py --calibrate
```

For 20 seconds it prints the loudness level twice a second, like this:

```
peak  -52.3 dBFS  ##############
peak  -18.7 dBFS  ##############################
```

Let the room be normally quiet for a few seconds, then get the dog to bark (ring the doorbell, knock, etc.). Note the numbers:
- Background noise might be around **−50**.
- Barks might be around **−20**.

Pick a threshold **between** those two, closer to the barks, for example **−25**. (Numbers closer to 0 are louder.)

### 5. Check the dispenser on its own

**Raspberry Pi with a relay:** connect the relay's signal wire to a GPIO pin (pin 17 in these examples). Then do a short dry run with no baseline and a 1-minute session:

```
python3 bark_trainer.py --gpio 17 --baseline 0 --duration 60
```

You should hear the dispenser go off every few seconds while the room is quiet.

**Smart / Wi-Fi dispenser:** replace the command in quotes with whatever triggers yours:

```
python3 bark_trainer.py --dispense-cmd "curl http://192.168.1.50/feed" --baseline 0 --duration 60
```

**No dispenser yet?** Leave out both options. The program will listen and print "TREAT" when it *would* have given one, so you can watch how it behaves first.

---

## Running a training session

A typical session:

```
python3 bark_trainer.py --gpio 17 --threshold -25 --log-file sessions.csv
```

What you'll see:

```
18:30:00 Baseline: listening for 300 s without rewarding...
18:35:00 Baseline: 36 barks (7.20/min). Starting interval: 4.2 s
18:35:04 TREAT #1 (earned with 4.2 s quiet); next needs 5.0 s
18:35:09 TREAT #2 (earned with 5.0 s quiet); next needs 6.0 s
18:35:12 BARK  -> reset; next treat after 4.2 s of quiet
```

To stop early, press **Ctrl + C**. The session also ends by itself after 1 hour or 150 treats, whichever comes first, and prints a summary.

---

## Settings you can change

Add any of these to the command. Example: `--growth 1.1 --max-treats 80`.

### Training settings

| Setting | Default | What it means |
|---|---|---|
| `--baseline` | `300` | Seconds to listen before training starts. Use `0` to skip the baseline and use `--initial` instead. |
| `--fraction` | `0.5` | Starting interval as a share of the dog's usual gap between barks. Lower = easier start. |
| `--initial` | `3` | Starting interval in seconds, used only when `--baseline 0`. |
| `--growth` | `1.2` | How much longer each interval gets after a treat (1.2 = 20% longer). Use 1.1 for a slower, gentler increase. |
| `--max-interval` | `900` | Longest quiet period it will ever ask for, in seconds (900 = 15 minutes). |
| `--min-interval` | `2` | Shortest interval allowed, so the dog has time to eat. |
| `--duration` | `3600` | Session length in seconds (3600 = 1 hour). |
| `--max-treats` | `150` | Session stops after this many treats. **Adjust for your dog's size and diet.** |

### Hardware settings

| Setting | Default | What it means |
|---|---|---|
| `--threshold` | `-25` | Loudness that counts as a bark (from calibration). Closer to 0 = only louder sounds count. |
| `--min-bark-ms` | `60` | A sound must stay loud this many milliseconds to count. Raise it if short clicks or bangs are counted as barks. |
| `--mute-after-treat` | `1.5` | Seconds to ignore sound after each treat, so the dispenser motor and crunching don't count as barks. |
| `--device` | system default | Which microphone to use, if you have more than one. |
| `--gpio` | none | Raspberry Pi pin connected to the dispenser relay. |
| `--pulse` | `0.3` | How long to switch the pin on per treat, in seconds. Increase it if the dispenser doesn't release a treat. |
| `--dispense-cmd` | none | Any command that dispenses one treat. |
| `--log-file` | none | Saves every bark and treat to a spreadsheet file (CSV). |

Run `python3 bark_trainer.py --help` to see the full list.

---

## Reading the results

If you use `--log-file sessions.csv`, every event is added to that file. Open it in Excel, Google Sheets, or Numbers. Each row has:

| Column | Meaning |
|---|---|
| Time | Date and time of the event |
| Event | `baseline`, `treat`, or `bark` |
| Count | Total barks or treats so far in the session (for `baseline`: barks counted) |
| Interval | The quiet interval in seconds after this event |

Across several days, you're hoping to see **fewer barks per session** and the **interval column climbing higher** before resets. The file keeps growing across sessions, so you can compare days.

---

## Safety and good practice

- **Watch the first few sessions.** Make sure the dog isn't stressed, the dispenser isn't jamming, and noises other than barks aren't being counted.
- **Count the calories.** At the default limit of 150 treats, use very small treats or part of the dog's daily food ration. Lower `--max-treats` for small dogs.
- **Keep sessions short at first** (`--duration 900` for 15 minutes) and build up.
- **Fresh water** should always be available.
- **Don't use it to replace care.** If the barking comes from anxiety, boredom, or pain, also deal with the cause, and consider a vet or certified trainer.
- **Check the dispenser can't trap a tongue or paw.**

---

## Known limitations

1. **It reacts to any loud sound, not just barks.** The program measures loudness only. A doorbell, a dropped pan, or loud voices can count as a bark and reset the timer. Calibrate carefully and place the microphone close to the dog. A future improvement would be a trained bark-recognition model.
2. **A bark drops all the way back to the start.** This was the original design. Many trainers prefer a softer reset that only steps back partway (for example to half the current interval), so one bark doesn't cancel a long run of progress. That's not built in yet; it would be a small change in the `on_bark` function in `bark_trainer.py`.
3. **Each session starts fresh.** Progress isn't carried over between sessions; the baseline is re-measured every time.
4. **Untested on real hardware.** The logic is tested in simulation only.

---

## Troubleshooting

| Problem | Try this |
|---|---|
| `Missing library (sounddevice)` | Repeat step 2 of setup. |
| `PortAudio library not found` | Raspberry Pi / Linux: `sudo apt install portaudio19-dev`, then reinstall `sounddevice`. |
| Every little noise counts as a bark | Move the threshold closer to 0 (e.g. from `-25` to `-20`) or raise `--min-bark-ms` to `100`. |
| Barks aren't detected | Move the threshold further from 0 (e.g. `-30`), or move the microphone closer to the dog. |
| Dispenser clicks but no treat comes out | Increase `--pulse` (e.g. `0.6`), or check the treats fit the dispenser. |
| Dispensing sound is counted as a bark | Increase `--mute-after-treat` (e.g. `3`). |
| Wrong microphone is used | Run `python3 -m sounddevice` to list microphones, then add `--device` followed by the number shown. |

---

## Files

| File | What it is |
|---|---|
| `bark_trainer.py` | The program. |
| `requirements.txt` | The add-on packages it needs. |
| `README.md` | This guide. |
