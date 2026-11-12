# Toyota 4A-GE 20V Silvertop

TunerStudio tune for a Speeduino ECU. The current calibration is [CurrentTune.msq](CurrentTune.msq).

## beta-v2

Semi-stable beta with validated AFR readings and stable idle with the A/C on. Spark timing was adjusted for fuel efficiency during light cruising and for wide-open throttle (WOT) operation.

### Observed AFR readings

| Driving condition | AFR range |
| --- | --- |
| Idle with A/C on | 14.4–15.9 |
| Idle with A/C off | 14.4–15.0 |
| Light cruising | 14.6–15.2 |
| Part throttle (overtaking) | 13.5–14.5 |
| Near WOT | 13.2–13.8 |
| WOT | 12.7–13.5 |

## Requirements

- Speeduino ECU with firmware 2025.01.6, as recorded in the tune
- Wideband O₂ sensor
- Laptop with TunerStudio
- USB-B cable with a USB-A or USB-C connector for the laptop

## v0.1

- Initial base tune and project configuration
