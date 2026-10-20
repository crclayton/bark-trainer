#!/usr/bin/env python3
"""
Bark-contingent treat dispenser.

Uses DRO (differential reinforcement of other behaviour) with an interval that grows each time:
  1. Baseline: listen without rewarding anything and measure how often the dog barks.
  2. Training: start the quiet interval well below the dog's average time between barks,
     so a treat arrives before it is likely to bark. Each treat earned stretches the
     next interval by `growth`, up to `max_interval`.
  3. Any bark resets the timer and drops the interval back to its starting value.

Hardware is optional:
  - Microphone: needs `pip install sounddevice numpy`. Without it, use --simulate.
  - Dispenser (see "Choosing a dispenser" in README.md for specific products):
      --gpio PIN          switch a relay/optocoupler, e.g. wired across a remote's button
      --servo PIN         spin a continuous-rotation servo (DIY 3D-printed dispensers)
      --dispense-cmd ".." run any command, e.g. mosquitto_pub or curl for Wi-Fi/Zigbee feeders
    With none of these, treats are only logged. --test-dispense fires one treat and exits.

Examples:
  python3 bark_trainer.py --simulate                    # test the logic, no hardware
  python3 bark_trainer.py --calibrate                   # show mic levels to pick a threshold
  python3 bark_trainer.py --gpio 17 --threshold -22     # run it for real
"""

import argparse
import csv
import logging
import queue
import random
import subprocess
import sys
import time
from dataclasses import dataclass

log = logging.getLogger("bark_trainer")


# ---------------------------------------------------------------- schedule logic

@dataclass
class ScheduleConfig:
    initial_interval: float = 3.0   # seconds of quiet needed for the first treat
    growth: float = 1.2             # interval multiplier after each earned treat
    max_interval: float = 900.0     # longest quiet period the schedule asks for
    min_interval: float = 2.0       # floor (gives the dog time to eat between treats)


def initial_interval_from_baseline(barks, seconds, fraction, cfg):
    """Start at `fraction` of the dog's mean inter-bark interval during the baseline."""
    mean_gap = seconds / max(barks, 1)   # no barks: treat the whole baseline as one gap
    return max(cfg.min_interval, min(fraction * mean_gap, cfg.max_interval))


class DROSchedule:
    """Pure timing logic; give it timestamps and it says when to dispense."""

    def __init__(self, cfg, now):
        self.cfg = cfg
        self.interval = cfg.initial_interval
        self.deadline = now + self.interval
        self.treats = 0
        self.barks = 0
        self.longest_interval_earned = 0.0

    def on_bark(self, now):
        self.barks += 1
        self.interval = self.cfg.initial_interval
        self.deadline = now + self.interval

    def due(self, now):
        """True if the dog has stayed quiet for the whole interval. Advances the schedule."""
        if now < self.deadline:
            return False
        self.treats += 1
        self.longest_interval_earned = max(self.longest_interval_earned, self.interval)
        self.interval = min(self.interval * self.cfg.growth, self.cfg.max_interval)
        self.deadline = now + self.interval
        return True


# ---------------------------------------------------------------- hardware

class BarkDetector:
    """Loudness-based detector: a bark is sound above `threshold_db` (dBFS) for at least
    `min_ms`. One bark won't be counted twice within `refractory` seconds."""

    def __init__(self, threshold_db=-25.0, min_ms=60, refractory=0.5,
                 samplerate=16000, block_ms=20, device=None):
        import numpy as np
        import sounddevice as sd
        self.np, self.sd = np, sd
        self.threshold_db = threshold_db
        self.blocks_needed = max(1, round(min_ms / block_ms))
        self.refractory = refractory
        self.events = queue.Queue()
        self.levels = queue.Queue(maxsize=200)
        self._loud_blocks = 0
        self._last_bark = 0.0
        self._ignore_until = 0.0
        self.stream = sd.InputStream(samplerate=samplerate, channels=1, device=device,
                                     blocksize=int(samplerate * block_ms / 1000),
                                     callback=self._callback)

    def _callback(self, indata, frames, time_info, status):
        rms = float(self.np.sqrt(self.np.mean(indata[:, 0] ** 2)))
        db = 20 * self.np.log10(rms + 1e-12)
        if not self.levels.full():
            self.levels.put_nowait(db)
        now = time.monotonic()
        if now < self._ignore_until:
            self._loud_blocks = 0
            return
        self._loud_blocks = self._loud_blocks + 1 if db >= self.threshold_db else 0
        if self._loud_blocks >= self.blocks_needed and now - self._last_bark >= self.refractory:
            self._last_bark = now
            self._loud_blocks = 0
            self.events.put(now)

    def ignore_for(self, seconds):
        """Ignore sound for a while, e.g. the dispenser motor and the dog eating."""
        self._ignore_until = time.monotonic() + seconds

    def __enter__(self):
        self.stream.start()
        return self

    def __exit__(self, *exc):
        self.stream.stop()
        self.stream.close()


class Dispenser:
    def __init__(self, gpio_pin=None, command=None, pulse=0.3, servo_pin=None):
        self.command, self.pulse, self.device, self.servo = command, pulse, None, None
        if gpio_pin is not None:
            from gpiozero import OutputDevice
            self.device = OutputDevice(gpio_pin)
        if servo_pin is not None:
            from gpiozero import Servo
            self.servo = Servo(servo_pin)
            self.servo.detach()

    def dispense(self):
        if self.device is not None:
            self.device.on()
            time.sleep(self.pulse)
            self.device.off()
        elif self.servo is not None:
            # Continuous-rotation servo: spin for `pulse` seconds, then stop.
            self.servo.max()
            time.sleep(self.pulse)
            self.servo.detach()
        elif self.command:
            subprocess.run(self.command, shell=True, check=False, timeout=10)
        else:
            log.info("(dry run: no dispenser configured)")


# ---------------------------------------------------------------- real-time session

def drain(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            return items


def run_live(args, cfg):
    events_out = EventLog(args.log_file)
    dispenser = make_dispenser(args)
    with BarkDetector(args.threshold, args.min_bark_ms, device=args.device) as mic:
        if args.baseline > 0:
            log.info("Baseline: listening for %.0f s without rewarding...", args.baseline)
            time.sleep(args.baseline)
            n = len(drain(mic.events))
            cfg.initial_interval = initial_interval_from_baseline(n, args.baseline, args.fraction, cfg)
            log.info("Baseline: %d barks (%.2f/min). Starting interval: %.1f s",
                     n, n / args.baseline * 60, cfg.initial_interval)
            events_out.write("baseline", n, cfg.initial_interval)

        start = time.monotonic()
        sched = DROSchedule(cfg, start)
        end = start + args.duration
        try:
            while time.monotonic() < end and sched.treats < args.max_treats:
                for t in drain(mic.events):
                    sched.on_bark(t)
                    log.info("BARK  -> reset; next treat after %.1f s of quiet", sched.interval)
                    events_out.write("bark", sched.barks, sched.interval)
                interval_just_earned = sched.interval
                if sched.due(time.monotonic()):
                    mic.ignore_for(args.mute_after_treat)
                    dispenser.dispense()
                    log.info("TREAT #%d (earned with %.1f s quiet); next needs %.1f s",
                             sched.treats, interval_just_earned, sched.interval)
                    events_out.write("treat", sched.treats, sched.interval)
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
    summarize(sched, time.monotonic() - start)
    events_out.close()


def make_dispenser(args):
    return Dispenser(args.gpio, args.dispense_cmd, args.pulse, args.servo)


def calibrate(args):
    log.info("Showing mic levels (dBFS) for %d s. Let the dog bark, then pick a threshold "
             "above the background noise and below the barks.", args.calibrate)
    with BarkDetector(threshold_db=0, device=args.device) as mic:
        end = time.monotonic() + args.calibrate
        while time.monotonic() < end:
            time.sleep(0.5)
            levels = drain(mic.levels)
            if levels:
                peak = max(levels)
                bar = "#" * max(0, int((peak + 80) / 2))
                print(f"peak {peak:6.1f} dBFS  {bar}")


# ---------------------------------------------------------------- simulation

def run_simulation(args, cfg):
    """Fake dog: barks at random (Poisson). Each treat earned lowers its bark rate a little."""
    rng = random.Random(args.seed)
    rate = 1 / args.sim_bark_gap          # barks per second
    dt = 0.1

    t, barks = 0.0, 0
    while t < args.baseline:
        if rng.random() < rate * dt:
            barks += 1
        t += dt
    if args.baseline > 0:
        cfg.initial_interval = initial_interval_from_baseline(barks, args.baseline, args.fraction, cfg)
    print(f"[baseline] {barks} barks in {args.baseline:.0f}s -> starting interval "
          f"{cfg.initial_interval:.1f}s")

    sched = DROSchedule(cfg, 0.0)
    t, next_report = 0.0, 600.0
    while t < args.duration and sched.treats < args.max_treats:
        t += dt
        if rng.random() < rate * dt:
            sched.on_bark(t)
            if args.verbose:
                print(f"{t:8.1f}s  BARK  reset to {sched.interval:.1f}s")
        elif sched.due(t):
            rate = max(rate * 0.97, 1 / 7200)
            if args.verbose:
                print(f"{t:8.1f}s  treat #{sched.treats}, next {sched.interval:.1f}s")
        if t >= next_report:
            print(f"[{t/60:5.0f} min] treats={sched.treats:4d} barks={sched.barks:4d} "
                  f"current interval={sched.interval:7.1f}s  dog's mean bark gap={1/rate:7.0f}s")
            next_report += 600
    summarize(sched, t)


# ---------------------------------------------------------------- shared helpers

class EventLog:
    def __init__(self, path):
        self.f = open(path, "a", newline="") if path else None
        self.w = csv.writer(self.f) if self.f else None

    def write(self, event, count, interval):
        if self.w:
            self.w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), event, count, f"{interval:.2f}"])
            self.f.flush()

    def close(self):
        if self.f:
            self.f.close()


def summarize(sched, elapsed):
    print(f"\nSession: {elapsed/60:.1f} min, {sched.treats} treats, {sched.barks} barks, "
          f"longest quiet interval rewarded: {sched.longest_interval_earned:.1f}s, "
          f"current interval: {sched.interval:.1f}s")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_argument_group("schedule")
    s.add_argument("--baseline", type=float, default=300, help="seconds to measure barking first (0 = skip)")
    s.add_argument("--fraction", type=float, default=0.5, help="starting interval as a fraction of the baseline bark gap")
    s.add_argument("--initial", type=float, default=3.0, help="starting interval if --baseline 0")
    s.add_argument("--growth", type=float, default=1.2, help="interval multiplier per earned treat")
    s.add_argument("--max-interval", type=float, default=900)
    s.add_argument("--min-interval", type=float, default=2.0)
    s.add_argument("--duration", type=float, default=3600, help="training session length in seconds")
    s.add_argument("--max-treats", type=int, default=150, help="stop after this many treats")
    h = p.add_argument_group("hardware")
    h.add_argument("--threshold", type=float, default=-25.0, help="bark loudness threshold, dBFS")
    h.add_argument("--min-bark-ms", type=float, default=60)
    h.add_argument("--device", default=None, help="sounddevice input device name or index")
    h.add_argument("--mute-after-treat", type=float, default=1.5,
                   help="ignore sound this many seconds after dispensing (motor, crunching)")
    h.add_argument("--gpio", type=int, help="Raspberry Pi GPIO pin that triggers the dispenser")
    h.add_argument("--servo", type=int, help="GPIO pin driving a continuous-rotation servo")
    h.add_argument("--pulse", type=float, default=0.3, help="seconds to hold the relay on / spin the servo")
    h.add_argument("--dispense-cmd", help="shell command that dispenses one treat")
    h.add_argument("--test-dispense", action="store_true", help="dispense one treat and exit")
    h.add_argument("--log-file", help="append events to this CSV")
    h.add_argument("--calibrate", type=int, nargs="?", const=20, help="show mic levels for N seconds and exit")
    m = p.add_argument_group("simulation")
    m.add_argument("--simulate", action="store_true", help="run against a fake dog, faster than real time")
    m.add_argument("--sim-bark-gap", type=float, default=10, help="fake dog's mean seconds between barks")
    m.add_argument("--seed", type=int, default=1)
    m.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = ScheduleConfig(args.initial, args.growth, args.max_interval, args.min_interval)

    if args.simulate:
        run_simulation(args, cfg)
        return
    try:
        if args.test_dispense:
            make_dispenser(args).dispense()
            log.info("Test dispense finished.")
        elif args.calibrate:
            calibrate(args)
        else:
            run_live(args, cfg)
    except ImportError as e:
        sys.exit(f"Missing library ({e.name}). Install with: pip install sounddevice numpy gpiozero\n"
                 f"Or try the logic without hardware: python3 {sys.argv[0]} --simulate")


if __name__ == "__main__":
    main()
