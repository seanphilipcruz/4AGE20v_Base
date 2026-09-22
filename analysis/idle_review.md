# Idle datalog review — 7 September 2026

Read-only review of CurrentTune.msq (saved 6 September 19:54:37) and both 6 September MLG logs. No calibration changed.

## Data validation

Decoded using EFI Analytics MLG version 2 specification, https://www.efianalytics.com/TunerStudio/docs/MLG_Binary_LogFormat_2.0.pdf . Field lengths match each 150-byte record; all 52,774 data-record checksums passed. Morning log: 1,731 records, 115.27 seconds. Evening: 51,043 records, elapsed time through 3567.73 seconds, with reset/offline markers. Records lacking a finite Time are excluded from timed analysis. Checksums validate bytes, not sensor accuracy. CSV exports and reproducible parser are in this directory.

## Saved configuration

Firmware 2025.01.6; PWM Closed+Open loop, 250 Hz, two channels, Reverse. P 1.21875, I 0.5, D 0.03124 in the saved INI-scaled units (display approximately 1.22 / 0.50 / 0.031). These are gains, not valve opening. Limits 35–100%. Base duty 48% at 60 C, 40% at 70–100 C. Target 1000 RPM at 60–90 C, 950 at 100 C. TPS idle threshold 2%. Idle advance Added, up to +3 degrees for positive idle error. Idle-up input disabled despite a stored 19-point adder; dedicated A/C control disabled and request/compressor pins unused. DFCO on, 1600 RPM threshold, 200 RPM hysteresis, zero delay.

Saved settings are not proven to have applied throughout either log: morning target is 1050 RPM with duty as low as 20%; evening frequently caps at 90% before later reaching 100%. Logs do not record PID gains or compressor state. Hardware valve wiring, actual firing order, injector size/impedance and trigger installation remain unverified.

## Evidence (elapsed times)

Morning: no zero-RPM stop, but closed-throttle dips reach 638 RPM. At 10–20 seconds RPM ranges 638–1094 and IAC 46–100%. Sync-loss counter remains 11; no new losses recorded.

Evening: seven transitions from nonzero RPM to zero; two coincide with gaps/resets and cannot be classified as captured stalls. Five show preceding RPM collapse, though intentional key-off cannot be excluded, especially the voltage-collapse event.

- 1269.26 s (21:09): after throttle closure, IAC remains 34% at 876 RPM, then rises to 86–90% as RPM falls below 550. Engine stops. DFCO is off during the collapse.
- 1419.34 s (23:39): initially about 1000 RPM and 68% IAC, then below 600 with 90% IAC before stopping.
- 1650.81 s (27:31): initially about 1000 RPM and 64–66% IAC; 568 RPM at 90% IAC, then zero. Voltage falls from 13.6 to about 12.7 V; first sync-count increase occurs after the large RPM drop.
- 2369.78 s (39:30): voltage channel falls from 13.6 V to 4.4 then 1.4 V while nonzero RPM remains recorded. Could be shutdown, supply/sensing fault, or invalid telemetry; not proof of actual battery voltage.
- 3406.13 s (56:46): RPM falls into the 500s while IAC reaches 100%; sync-loss count subsequently increases. Zero RPM follows.

At 2540–2550 s, TPS remains zero, RPM ranges 856–1284 and IAC 58–100%. At 2550–2560 s, RPM ranges 652–1120 and IAC 58–100%. This confirms large idle fluctuations, but does not distinguish compressor cycling from self-excited PID hunting.

AFR and lambda remain zero. At the 27:31 event, PW grows from about 2.72 to 5.33 ms as MAP rises from about 40 to 97 kPa and RPM collapses. Pulse width alone cannot determine mixture richness. Fuel cut is not active during the observed continuous collapses. Sync loss may worsen stalls, but its timing does not establish it as the initial cause.

## Recommended diagnostic order

1. Stationary testing: verify ECU/engine grounds, sensor grounds, valve supply and charging voltage with A/C cycling. Compare a meter at the ECU supply against logged voltage. Inspect compressor load and trigger wiring; use tooth/composite logging if sync faults persist above cranking speeds.
2. Identify the IAC valve and driver wiring. Verify small commanded duty increases produce more airflow/RPM in open loop. Reverse and two-channel settings cannot be judged from their names alone. Do not blindly reverse polarity or sweep to full duty.
3. Establish stable warm open-loop airflow, then measure extra duty required with A/C. Wire/validate a suitable A/C request input before configuring idle-up; the stored 19-point adder is not an established calibration. Firmware uses base airflow plus PID correction and supports A/C feed-forward: https://raw.githubusercontent.com/speeduino/speeduino/202501.6/speeduino/idle.cpp .
4. Tune PID only after valve response and base airflow are established. Compare fixed-airflow and closed-loop behavior under the same stationary conditions. If hunting is specific to closed loop, isolate P with I/D temporarily zero, establish damped response, then add I gradually. Do not assign final gains from these logs.
5. Obtain measured AFR/lambda before leaning VE/WUE or attributing rough combustion to excess fuel. Check plugs, ignition and throttle synchronization as other possible causes of roughness.

Follow-up needed: IAC valve type/part number and wiring; whether any voltage-collapse/reset events were deliberate key-offs; A/C on/off event markers in the next log.
