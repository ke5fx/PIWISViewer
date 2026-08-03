#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# piwis_val_viewer.py
#
# Standalone viewer for PIWIS 3 "Vehicle Analysis Log" (VAL / FAP) archives.
#
# What it does:
#   1) Presents a file-selection dialog to accept a zipfile containing a
#      PIWIS vehicle analysis log (FAP_*.xml plus stylesheets).
#   2) Renders the XML log into a human-readable, self-contained HTML file
#      in the system temp directory.
#   3) Launches the default web browser to display it.
#
# Requires only the Python 3 standard library (tkinter for the dialog).
# The generated HTML is pure ASCII; any non-ASCII characters in the log
# are emitted as numeric character references.
#
# Command line (all optional; no arguments = normal GUI operation):
#   piwis_val_viewer.py [input.zip|input.xml] [--out FILE] [--no-open]
# ---------------------------------------------------------------------------

import argparse
import html
import os
import sys
import tempfile
import time
import webbrowser
import zipfile
import xml.etree.ElementTree as ET

APP_NAME = "PIWIS VAL Viewer"

# Log-type codes used in RESULT/HEADER/PROTOKOLLTYPE (per PIWIS stylesheet)
PROTOCOL_TYPES = {
    "BEFOREREP": "Pre-VAL (before repair)",
    "AFTERREP": "Post-VAL (after repair)",
    "WHILEREP": "Interim-VAL (during repair)",
}

# Friendly English titles for the German MEAS/@OBJECT group names
MEAS_OBJECT_TITLES = {
    "Identifikation": "Identification",
    "Codierung": "Coding",
    "Fehler": "Fault memory",
    "Erweiterter Fehlerspeicher": "Extended fault memory",
    "Messwerte": "Measured values",
    "Istwerte": "Actual values",
    "Fahrzeugdaten": "Vehicle data",
    "Statistikwerte": "Statistics",
    "Adaptionswerte": "Adaptation values",
}

# ===========================================================================
# German -> English translation
# ===========================================================================
# The PIWIS log labels (VALUE/@TEXT), some VALUE contents, and some units
# are German. The dictionaries below translate them. HOW IT WORKS:
#
#   1. The input string is normalized: umlauts are transliterated to ASCII
#      (ae oe ue ss), dashes are unified, whitespace is collapsed. All
#      dictionary keys below are written in that normalized ASCII form, so
#      this file stays plain ASCII and both spellings found in the logs
#      (umlaut "Schl?ssel" and transliterated "Schluessel", where ? is
#      u-umlaut) hit the same key.
#   2. Lookup order for a label:
#        a. TRANS_LABEL   - the entire normalized label, exact match
#        b. TRANS_SEGMENT - the label is split into segments on ": " and
#           " - "; each segment, with every digit run replaced by '#',
#           is looked up. The English value may contain '#' placeholders
#           which are refilled with the original numbers in order.
#        c. TRANS_PHRASE  - longest-match word/phrase replacement inside
#           any segment not matched above (case-insensitive, whole-word).
#      Anything that matches nothing is left in the original language.
#   3. VALUE contents go through TRANS_VALUE (exact match) first, then the
#      same segment/phrase fallback. Units use TRANS_UNIT only.
#
# TO EXTEND: add entries to the appropriate dict. Write keys in ASCII
# (ae oe ue ss for umlauts), collapse runs of spaces to one space, and for
# TRANS_SEGMENT replace every digit run with '#' (same count of '#' in the
# English text). TRANS_PHRASE keys must be lowercase.
# ===========================================================================

TRANSLIT = {
    0xE4: "ae", 0xF6: "oe", 0xFC: "ue", 0xDF: "ss",
    0xC4: "Ae", 0xD6: "Oe", 0xDC: "Ue",
    0x2013: "-", 0x2014: "-", 0xA0: " ",
    0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"',
}


def norm_de(text):
    """Normalize a string for dictionary lookup (see block comment above)."""
    if not text:
        return ""
    import re as _re
    return _re.sub(r"\s+", " ", str(text).translate(TRANSLIT)).strip()


# --- whole labels (exact match, highest priority) --------------------------
TRANS_LABEL = {
    "Shift-/Keylock Magnet Position unplausibel(Invalid Signal)":
        "Shift/keylock magnet position implausible (Invalid Signal)",
    "Steuergeraet EPB - DAR nicht verfuegbar":
        "Control unit EPB - DAR (drive-away release) not available",
    "Lin Botschaftsfehler BKE-Tastenfeld":
        "LIN message fault, climate control keypad",
    "Displaykontroller Watchdog Timeout":
        "Display controller watchdog timeout",
    "Anzeige der der angelernten Komponenten":
        "Display of the learned components",
    "FH-Motor Rippelqualitaet 1-6, Kisi - Motor, FH - Motor - Strom":
        "Window motor ripple quality 1-6, Kisi motor, window motor current",
}

# --- label segments, digit runs replaced by '#' ----------------------------
TRANS_SEGMENT = {
    "Crashdaten von Crashereignis #": "Crash data from crash event #",
    "Crashdaten Teil #": "Crash data part #",
    "Crashdaten Teil # erfolgreich gelesen":
        "Crash data part # read successfully",
    "Teil #": "Part #",
    "Wert #": "Value #",
    "Warnung #": "Warning #",
    "Aktive Warnung #": "Active warning #",
    "Bordcomputer Anzeigenkonfiguration Schluessel #":
        "On-board computer display configuration, key #",
    "Bordcomputer Anzeigenkonfiguration Werkseinstellung":
        "On-board computer display configuration, factory setting",
    "Bordcomputer Menuekonfiguration": "On-board computer menu configuration",
    "Bordcomputer Menuekonfiguration #":
        "On-board computer menu configuration #",
    "Bordcomputer Menuekonfiguration Byte #":
        "On-board computer menu configuration byte #",
    "Bordcomputer vorne": "On-board computer front",
    "Bordcomputer hinten": "On-board computer rear",
    "Diagnose-Monitoring Pruefbedingungen":
        "Diagnostic monitoring test conditions",
    "Pruefbedingungen im aktuellen Fahrzyklus erfuellt (#-#)":
        "Test conditions fulfilled in current driving cycle (#-#)",
    "Diagnose-Monitoring Pruefungen": "Diagnostic monitoring tests",
    "Pruefung im aktuellen Fahrzyklus durchgefuehrt (#-#)":
        "Test performed in current driving cycle (#-#)",
    "Datum und Zeit": "Date and time",
    "Kennlinien und Schwellen": "Characteristic curves and thresholds",
    "Zuendmaske #": "Ignition mask #",
    "Zuendkreisdiagnose #": "Ignition circuit diagnosis #",
    "Zuendkreisansteuerung #": "Ignition circuit activation #",
    "Zuendanforderung #": "Ignition request #",
    "kompatible Aenderung der freigegebenen Version_#":
        "compatible change of the released version_#",
    "kompatible Aenderung der freigegebenen Version":
        "compatible change of the released version",
    "Freigegebene Version_#": "Released version_#",
    "Freigegebene Version": "Released version",
    "Fehler # (nicht belegt)": "Fault # (not assigned)",
    "Schluessel #": "Key #",
    "Schluessel": "Key",
    "allgemeiner Schluessel Status": "General key status",
    "Leistungs- und Drehmomentanzeige": "Power and torque display",
    "Satellitenkommunikation # ms vor crash":
        "Satellite communication # ms before crash",
    "Satellitenkommunikation # us vor crash":
        "Satellite communication # us before crash",
    "Satellitenkommunikation # ms nach crash":
        "Satellite communication # ms after crash",
    "Satellitenkommunikation # us nach crash":
        "Satellite communication # us after crash",
    "KS Fehlerliste Byte #": "KS fault list byte #",
    "Ks Fehlerliste Byte #": "KS fault list byte #",
    "KS Liste Byte #": "KS list byte #",
    "Interne Signale des Steuergeraets":
        "Internal signals of the control unit",
    "Geschwindigkeit": "Speed",
    "Wachhaltegruende": "Keep-awake reasons",
    "Letzter Kl#-aus Zyklus": "Last Kl#-off cycle",
    "Letzter Kl#-aus Zyklus Kl#-aus Zeitstempel":
        "Last Kl#-off cycle, Kl#-off timestamp",
    "Letzter Kl#-aus Zyklus Kl#-ein Zeitstempel":
        "Last Kl#-off cycle, Kl#-on timestamp",
    "Worst Case Zyklus Klemme S ein": "Worst-case cycle, terminal S on",
    "Worst Case Zyklus Klemme S ein Kl#-aus Zeitstempel":
        "Worst-case cycle terminal S on, Kl#-off timestamp",
    "Worst Case Zyklus Klemme S ein Kl#-ein Zeitstempel":
        "Worst-case cycle terminal S on, Kl#-on timestamp",
    "Worst Case Zyklus nicht schlafbereiter SG":
        "Worst-case cycle, control unit not ready to sleep",
    "Worst Case Zyklus nicht schlafbereiter SG Kl#-aus Zeitstempel":
        "Worst-case cycle ECU not ready to sleep, Kl#-off timestamp",
    "Worst Case Zyklus nicht schlafbereiter SG Kl#-ein Zeitstempel":
        "Worst-case cycle ECU not ready to sleep, Kl#-on timestamp",
    "Worst Case Zylus nicht schlafbereiter Steuergeraete":
        "Worst-case cycle, control units not ready to sleep",
    "Worst Case Zyklus Wake-Up": "Worst-case cycle wake-up",
    "Worst Case Zyklus WakeUp Kl#-aus Zeitstempel":
        "Worst-case cycle wakeup, Kl#-off timestamp",
    "Worst Case Zyklus WakeUp Kl#-ein Zeitstempel":
        "Worst-case cycle wakeup, Kl#-on timestamp",
    "Worst Case Zyklus Kommunikationsdauer":
        "Worst-case cycle communication duration",
    "Worst Case Zyklus Kommunikationsdauer Kl#-aus Zeitstempel":
        "Worst-case cycle communication duration, Kl#-off timestamp",
    "Worst Case Zyklus Kommunikationsdauer Kl#-ein Zeitstempel":
        "Worst-case cycle communication duration, Kl#-on timestamp",
    "Pinzustaende": "Pin states",
    "Ersatzmassnahmen": "Substitute measures",
    "HIST Ruhestromueberschreitung": "HIST quiescent current exceedance",
    "Ruhestromueberschreitung_#": "Quiescent current exceedance_#",
    "HIST Ruhespannungsunterschreitung":
        "HIST quiescent voltage undershoot",
    "Ruhespannungsunterschreitung_#,#V_#":
        "Quiescent voltage undershoot_#.#V_#",
    "Warnung-ID": "Warning ID",
    "Warnung im Vordergrund": "Warning in foreground",
    "Wartet auf die Erstanzeige": "Waiting for first display",
    "Wartet auf die Displayzuteilung": "Waiting for display allocation",
    "begrentzte Anzeige abgelaufen": "Limited display period expired",
    "FFB Schluessel-Status": "FFB (remote control) key status",
    "Fehlerspeicherliste (KD-Bit)": "Fault memory list (KD bit)",
    "Analyse der aktuellen Fehlerursache":
        "Analysis of the current fault cause",
    "Analyse der allgemeinen Fehlerursache":
        "Analysis of the general fault cause",
    "Digitale Messwerte lesen": "Read digital measured values",
    "Status Steuergeraeteinterne Spannung":
        "Status of control-unit-internal voltage",
    "Taste # betaetigt": "Button # pressed",
    "HIST Chronik fuer Batteriebeeinflussung":
        "HIST history of battery influence",
    "Chronik fuer Batteriebeeinflussung_#":
        "History of battery influence_#",
    "HIST Anzahl Wakeups d. BEM / ForceSleep":
        "HIST number of wakeups by BEM / ForceSleep",
    "Data Record # Zaehler_Klasse": "Data record # counter_class",
    "Crashzaehler #": "Crash counter #",
    "Gesp. Zaehlw. FS# (SLV, LNV, SHV, SNV)":
        "Stored count FS# (SLV, LNV, SHV, SNV)",
    "Gesp. Zaehlw. P# (SLV, LNV, SHV, SNV)":
        "Stored count P# (SLV, LNV, SHV, SNV)",
    "Gesp. Zaehlw. FS# (Lordose)": "Stored count FS# (lumbar)",
    "Gesp. Zaehlw. P# (Lordose)": "Stored count P# (lumbar)",
    "Gesp. Zaehlw. P# (STV, LDH mech., LDW mech.)":
        "Stored count P# (STV, LDH mech., LDW mech.)",
    "Gesp. Zaehlw. FS# (STV, LDH mech., LDW mech.)":
        "Stored count FS# (STV, LDH mech., LDW mech.)",
    "Gesp. Zaehlw. P#, P#, P#, P# (STV)":
        "Stored count P#, P#, P#, P# (STV)",
    "Gesp. Zaehlw. FS#, FS#, FS#, FS# (STV)":
        "Stored count FS#, FS#, FS#, FS# (STV)",
    "Kuehlmitteltemperatur": "Coolant temperature",
    "Datum #": "Date #",
    "Datum": "Date",
    "Spannung": "Voltage",
    "Warnungen Motor": "Engine warnings",
    "Eingangssignale der Peripherie": "Input signals from peripherals",
    "Ausgangssignale der Peripherie": "Output signals to peripherals",
    "HIST Reduzierung Innengeblaese": "HIST reduction of interior blower",
    "Reduzierung Innengeblaese_#": "Reduction of interior blower_#",
    "Thoraxairbag hinten Fahrerseite": "Thorax airbag rear, driver side",
    "Thoraxairbag hinten Beifahrerseite":
        "Thorax airbag rear, passenger side",
    "Gurtstraffer hinten Fahrerseite": "Belt tensioner rear, driver side",
    "Gurtstraffer hinten Beifahrerseite":
        "Belt tensioner rear, passenger side",
    "Gurtstraffer hinten Mitte": "Belt tensioner rear, center",
    "Ueberrollschutz Fahrerseite": "Rollover protection, driver side",
    "Ueberrollschutz Beifahrerseite": "Rollover protection, passenger side",
    "Lenksaeule Kraft-Wegsteuerung": "Steering column force/travel control",
    "A-Saeule Fahrerseite, Y-Richtung": "A-pillar driver side, Y direction",
    "A-Saeule Fahrerseite, X-Richtung": "A-pillar driver side, X direction",
    "A-Saeule Beifahrerseite, Y-Richtung":
        "A-pillar passenger side, Y direction",
    "A-Saeule Beifahrerseite, X-Richtung":
        "A-pillar passenger side, X direction",
    "C-Saeule Fahrerseite, Y-Richtung": "C-pillar driver side, Y direction",
    "C-Saeule Fahrerseite, X-Richtung": "C-pillar driver side, X direction",
    "C-Saeule Beifahrerseite, Y-Richtung":
        "C-pillar passenger side, Y direction",
    "C-Saeule Beifahrerseite, X-Richtung":
        "C-pillar passenger side, X direction",
    "Fussgaengerschutzsensor Beifahrerseite":
        "Pedestrian protection sensor, passenger side",
    "Fussgaengerschutzsensor Fahrerseite":
        "Pedestrian protection sensor, driver side",
    "OBD Monitor Status seit Fehlerspeicher loeschen":
        "OBD monitor status since fault memory clearing",
    "Widerstaende, Qualitaet, Fehlerbits, ADC":
        "Resistances, quality, fault bits, ADC",
    "Gesamtdauer Kl#-ein (h)": "Total duration Kl#-on (h)",
    "Oeldruck": "Oil pressure",
    "Oelstand": "Oil level",
    "Oelstand Anzeigeschwellen": "Oil level display thresholds",
    "Verfuegbarkeit": "Availability",
    "Interne FehlerSpekIndizes von CAN-Signalfehler LWS":
        "Internal fault-spec indices of CAN signal fault LWS",
    "Interne FehlerSpekIndizes von Datensatz":
        "Internal fault-spec indices of data record",
    "Interner FehlerSpekIndizes von SG Defekt":
        "Internal fault-spec indices of ECU defect",
    "Bilanz der letzten Fahrt (Ah)": "Balance of the last trip (Ah)",
    "Dauer CAN ein (h)": "Duration CAN on (h)",
    "Dauer (h)": "Duration (h)",
    "WU Teil #": "WU part #",
    "Zaehler": "Counter",
    "Zaehler #": "Counter #",
    "BC-Konfiguration Byte # BC-Seite Fahrzeug Zeile #":
        "BC configuration byte #, BC page vehicle, line #",
    "Motoroeltemperatur": "Engine oil temperature",
    "Temperatur": "Temperature",
    "Zeitstempel Datum": "Timestamp date",
    "Zeitstempel": "Timestamp",
    "Standheizung inaktiv/aktiv": "Auxiliary heating inactive/active",
    "Beleuchtungsstaerke IN": "Illuminance IN",
    "Ueberspannungskonfiguration Nacht":
        "Overvoltage configuration, night",
    "Serviceintervallverlaengerung": "Service interval extension",
    "SW-Stand SG-Programm": "SW version, ECU program",
    "SW-Stand Bolo": "SW version Bolo",
    "Rad-IDs postitionsbezogen lesen vorne links":
        "Read wheel IDs by position, front left",
    "Rad-IDs postitionsbezogen lesen vorne rechts":
        "Read wheel IDs by position, front right",
    "Rad-IDs postitionsbezogen lesen hinten links":
        "Read wheel IDs by position, rear left",
    "Rad-IDs postitionsbezogen lesen hinten rechts":
        "Read wheel IDs by position, rear right",
    "Batterie neu": "Battery new",
    "HIST Chronik fuer sporadische Generatorfehler":
        "HIST history of sporadic alternator faults",
    "Kennlinie G-Force": "Characteristic curve G-force",
    "Kennlinie Geschwindigkeitskorrektur":
        "Characteristic curve, speed correction",
    "Kuehlwassertemperatur Kennlinie":
        "Coolant temperature characteristic curve",
    "Sonnenrollo(Dach)-Codierung (MD#)": "Sun blind (roof) coding (MD#)",
    "HIST kein Start/Stop Betrieb": "HIST no start/stop operation",
    "Software nicht konsistent": "Software not consistent",
    "Hoehen": "Heights",
    "Getriebe": "Transmission",
    "Status (#.Byte)": "Status (byte #)",
    "Zustaende (#.Byte)": "States (byte #)",
    "Bedienhoerer": "Handset",
    "Tankplausibiltaet": "Tank plausibility",
    "Warnungen BCM_V und SWSG": "Warnings BCM_V and SWSG",
    "Warnungen BCM_vorne": "Warnings BCM front",
    "Verlernzaehler": "Unlearn counter",
    "Betriebszustaende": "Operating states",
    "Haeufigkeitszaehler": "Frequency counter",
    "Daempferregelung": "Damper control",
    "Betriebsdauerzaehlerstand beim Einschalten der Warnlampe":
        "Operating time counter reading when warning lamp switched on",
    "Betriebsdauerzaehler": "Operating time counter",
    "Gurtschlossstatus hinten, Mitte": "Belt buckle status rear, center",
    "Gurtschlossstatus hinten, Beifahrerseite":
        "Belt buckle status rear, passenger side",
    "Gurtschlossstatus hinten, Fahrerseite":
        "Belt buckle status rear, driver side",
    "Gurtschlossstatus vorne, Beifahrerseite":
        "Belt buckle status front, passenger side",
    "Gurtschlossstatus vorne, Fahrerseite":
        "Belt buckle status front, driver side",
    "Fertigungsdaten Getriebe": "Transmission production data",
    "Oeldruckregelung": "Oil pressure control",
    "Saeureschichtung (%)": "Acid stratification (%)",
    "SBE/AKSE Byte #": "SBE/AKSE byte #",
    "Sitz Fahrer": "Driver seat",
    "Sitz Beifahrer": "Passenger seat",
    "Anhaenger": "Trailer",
    "Tuer Fahrer": "Driver door",
    "Tuer Beifahrer": "Passenger door",
    "Verdecksteuergeraet": "Convertible top control unit",
    "Bedien-Klima": "Climate control panel",
    "Rueckfahrkamera": "Reversing camera",
    "Motor": "Engine",
    "Waehlhebel": "Selector lever",
    "Systemzustaende": "System states",
    "PWM Ausgaenge": "PWM outputs",
    "Ausgaenge": "Outputs",
    "LD unten befuellen Filterwert #": "LD lower fill, filter value #",
    "LD unten entlueften Filterwert #": "LD lower vent, filter value #",
    "LD oben befuellen Filterwert #": "LD upper fill, filter value #",
    "LD oben entlueften Filterwert #": "LD upper vent, filter value #",
    "Verstellweg Ein/Ausstiegshilfe": "Adjustment travel, entry/exit aid",
    "Motortemperatur # (nach Thermomodell)":
        "Engine temperature # (per thermal model)",
    "Status Gurtschloesser (qualifiziert)":
        "Belt buckle status (qualified)",
    "Geschwindigkeitsstuetzstelle Schwenkwinkelbegrenzung [#]":
        "Speed interpolation point, swivel angle limit [#]",
    "Begrenzungsfaktorstuetzstelle Schwenkwinkelbegrenzung [#]":
        "Limiting factor interpolation point, swivel angle limit [#]",
    "Pruefung durchgefuehrt": "Test performed",
    "Autonome Pruefbarkeit": "Autonomous testability",
    "Fehlersymptom bei erstem Auftreten":
        "Fault symptom at first occurrence",
    "Zaehler Warm Up Cycle": "Warm-up cycle counter",
    "Markierter Fehler": "Marked fault",
    "BattTempUeberschreitung # [h]": "Battery temp exceedance # [h]",
    "Zaehler LLA_Stufe #": "Counter LLA_stage #",
    "Zaehler LLA_Stufe#": "Counter LLA_stage #",
    "Wakeup Zaehler": "Wakeup counter",
    "Zaehler Stopp verbieten": "Counter: prohibit stop",
    "Batterie Saeure Temp": "Battery acid temp",
    "Zaehler Start anfordern": "Counter: request start",
    "Motor Start anfordern [Strom": "Engine start request [current",
    "BC-Konfiguration Byte # Speed Limit #":
        "BC configuration byte #, speed limit #",
    "Schwelle fuer Speed-Limit-Warnung #":
        "Threshold for speed limit warning #",
    "Oelservice": "Oil service",
    "Pruefsumme": "Checksum",
    "Weitere Fehler": "Further faults",
    "Ueberspannungsschwelle": "Overvoltage threshold",
    "Fehler": "Fault",
    "FH Schalter hinten": "Window switch rear",
    "Funkschluessel aktivieren": "Activate radio key",
    "Schluessel Funkschluessel #": "Key, radio key #",
    "Sitzlaengsverst. / Lehnenneigungsverst.":
        "Seat longitudinal adj. / backrest tilt adj.",
    "Sitzhoehenverst. / Sitzneigungsverst.":
        "Seat height adj. / seat tilt adj.",
    "Gesp. Position Lordose oben P# (belasteter Sitz)":
        "Stored position lumbar upper P# (loaded seat)",
    "Gesp. Position Lordose unten P# (belasteter Sitz)":
        "Stored position lumbar lower P# (loaded seat)",
    "Gesp. Position Lordose oben P# (unbelasteter Sitz)":
        "Stored position lumbar upper P# (unloaded seat)",
    "Gesp. Position Lordose unten P# (unbelasteter Sitz)":
        "Stored position lumbar lower P# (unloaded seat)",
    "Gesp. Position Lordose oben FS# (belasteter Sitz)":
        "Stored position lumbar upper FS# (loaded seat)",
    "Gesp. Position Lordose unten FS# (belasteter Sitz)":
        "Stored position lumbar lower FS# (loaded seat)",
    "Gesp. Position Lordose oben FS# (unbelasteter Sitz)":
        "Stored position lumbar upper FS# (unloaded seat)",
    "Gesp. Position Lordose unten FS# (unbelasteter Sitz)":
        "Stored position lumbar lower FS# (unloaded seat)",
    "Lordosenverstellung Druck": "Lumbar adjustment pressure",
    "Entwickler Steuergeraete-Status": "Developer ECU status",
    "Softwareblock + Info Sitzlaengenverst.":
        "Software block + info, seat longitudinal adj.",
    "Softwareblock + Info Sitzhoehenverst.":
        "Software block + info, seat height adj.",
    "Lastwechselzaehler # (Gesamte Verstellungen)":
        "Load cycle counter # (total adjustments)",
    "Lastwechselzaehler # (Gesamte Vertellungen)":
        "Load cycle counter # (total adjustments)",
    "Aussentemperatur": "Outside temperature",
    "Bit Optionen Reserve(Bit#)": "Bit options, reserve (bit #)",
    "Bit Optionen Schrittlaenge": "Bit options, step length",
    "Bit Optionen Halten Schrittlaenge": "Bit options, hold step length",
    "Bit Optionen Master Schrittmotordrehrichtung invertiert":
        "Bit options, master stepper motor direction inverted",
    "Bit Optionen Slave Schrittmotordrehrichtung invertiert":
        "Bit options, slave stepper motor direction inverted",
    "Vorderachssensor ADC-Stuetzstelle [#]":
        "Front axle sensor, ADC interpolation point [#]",
    "Vorderachssensor Einfederung Stuetzstelle [#]":
        "Front axle sensor, compression interpolation point [#]",
    "Hinterachssensor ADC-Stuetzstelle [#]":
        "Rear axle sensor, ADC interpolation point [#]",
    "Hinterachssensor Einfederung Stuetzstelle [#]":
        "Rear axle sensor, compression interpolation point [#]",
    "Schwenkempfindlichkeit Stuetzstelle [#]":
        "Swivel sensitivity, interpolation point [#]",
    "Geschwindigkeitsstuetzstelle Knickwinkelkennlinie [#]":
        "Speed interpolation point, kink angle characteristic [#]",
    "Radwinkelstuetzstelle Knickwinkelkennlinie [#]":
        "Wheel angle interpolation point, kink angle characteristic [#]",
    "Sportmodus Geschwindigkeitsstuetzstelle Knickwinkelkennlinie [#]":
        "Sport mode speed interpolation point, kink angle characteristic [#]",
    "Sportmodus Radwinkelstuetzstelle Knickwinkelkennlinie [#]":
        "Sport mode wheel angle interpolation point, kink angle "
        "characteristic [#]",
    "Geschwindigkeitsstuetzstelle Schwenkempfindlichkeit [#]":
        "Speed interpolation point, swivel sensitivity [#]",
    "Gierratengewichtung Stuetzstelle [#]":
        "Yaw rate weighting, interpolation point [#]",
    "Schwenkwinkelstuetzstellen Grad Nichtlinearitaet [#]":
        "Swivel angle interpolation points, degrees non-linearity [#]",
    "Schwenkwinkelstuetzstellen Radiant Nichtlinearitaet [#]":
        "Swivel angle interpolation points, radians non-linearity [#]",
    "Sportmodus Schwenkwinkelstuetzstelle Grad Nichtlinearitaet [#]":
        "Sport mode swivel angle interpolation point, degrees "
        "non-linearity [#]",
    "Sportmodus Schwenkwinkelstuetzstelle Radiant Nichtlinearitaet [#]":
        "Sport mode swivel angle interpolation point, radians "
        "non-linearity [#]",
    "Geschwindigkeitsstuetzstelle Gierratengewichtung [#]":
        "Speed interpolation point, yaw rate weighting [#]",
    "Zuendwinkel": "Ignition angle",
    "Batterie leer (via FFB)": "Battery empty (via FFB)",
    "Batterie leer": "Battery empty",
    "Schluesseltyp": "Key type",
    "Schluesselmodus": "Key mode",
    "Reifenumfaenge (Geschwindigkeit Wegstrecke)":
        "Tire circumferences (speed, distance)",
    "Passwort war gueltig": "Password was valid",
    "Signatur war gueltig": "Signature was valid",
    "Bedingungen Anlernvorgang SW-Endanschlaege":
        "Conditions for teach-in of SW end stops",
    "Steuergeraet in Diagnose": "Control unit in diagnosis",
    "Wisch/Wasch-Parameter": "Wipe/wash parameters",
    "INFO zu Rekuperation Einschraenkungen":
        "INFO on recuperation restrictions",
    "RekuEinschraenkungen": "Recuperation restrictions",
    "Position der Analoganzeigen": "Position of analog gauges",
    "Versorgungsspannung ausserhalb des Betriebsbereiches":
        "Supply voltage outside operating range",
    "Zuendspannung oberhalb des zulaessigen Bereiches":
        "Ignition voltage above permissible range",
    "Zuendspannung unterhalb des zulaessigen Bereiches":
        "Ignition voltage below permissible range",
    "Zuendspannung ausserhalb des zulaessigen Bereiches":
        "Ignition voltage outside permissible range",
    "Zuendspannung innerhalb des Bereiches fuer Zuend-ASIC-Ueberwachung":
        "Ignition voltage within range for ignition ASIC monitoring",
    "Zuendspannung innerhalb des Bereiches fuer NVM-Schreibzugriffe":
        "Ignition voltage within range for NVM write accesses",
    "Satellitenversorgungsspannung oberhalb des zulaessigen Bereiches":
        "Satellite supply voltage above permissible range",
    "Satellitenversorgungsspannung unterhalb des zulaessigen Bereiches":
        "Satellite supply voltage below permissible range",
    "Sitzbelegungsstatus fuer Gurtwarnung":
        "Seat occupancy status for belt warning",
    "CAN-Ueberwachung aktiv": "CAN monitoring active",
    "Geschwindigkeitsinformation gueltig": "Speed information valid",
    "Steuergeraet ist im BusOff-Zustand":
        "Control unit is in bus-off state",
    "Bisherige Anzahl Crashes": "Number of crashes so far",
    "Bisherige Anzahl Fussgaengerschutzausloesungen":
        "Number of pedestrian protection deployments so far",
    "Bisherige Anzahl Front/Heck-Crashes":
        "Number of front/rear crashes so far",
    "Bisherige Anzahl Crashes Fahrerseite":
        "Number of driver-side crashes so far",
    "Bisherige Anzahl Crashes Beifahrerseite":
        "Number of passenger-side crashes so far",
    "Bisherige Anzahl Crashes Rollover/Pitchover":
        "Number of rollover/pitchover crashes so far",
    "Stromflussdauer Thoraxairbag hinten Fahrerseite":
        "Current flow duration, thorax airbag rear driver side",
    "Stromflussdauer Thoraxairbag hinten Beifahrerseite":
        "Current flow duration, thorax airbag rear passenger side",
    "Stromflussdauer Gurtstraffer hinten Fahrerseite":
        "Current flow duration, belt tensioner rear driver side",
    "Stromflussdauer Gurtstraffer hinten Beifahrerseite":
        "Current flow duration, belt tensioner rear passenger side",
    "Stromflussdauer Gurtstraffer hinten Mitte":
        "Current flow duration, belt tensioner rear center",
    "Stromflussdauer Ueberrollschutz Fahrerseite":
        "Current flow duration, rollover protection driver side",
    "Stromflussdauer Ueberrollschutz Beifahrerseite":
        "Current flow duration, rollover protection passenger side",
    "Stromflussdauer Lenksaeule Kraft-Wegsteuerung":
        "Current flow duration, steering column force/travel control",
    "PWM-Signal mindestens fuer eine Sekunde gesendet":
        "PWM signal sent for at least one second",
    "CAN-Signal mindestens fuer eine Sekunde gesendet":
        "CAN signal sent for at least one second",
    "Winkel zwischen optischer Achse und harten Anschlag":
        "Angle between optical axis and hard stop",
    "Winkel zwischen optischer Achse und weichen Anschlag":
        "Angle between optical axis and soft stop",
    "Uebertragungsfaktor HS": "Transfer factor HS",
    "Uebertragungsfaktor Winkel": "Transfer factor angle",
    "Zeit vor/nach Motorbestromung":
        "Time before/after motor energization",
    "Halbierung der Stromwerte fuer bessere Aufloesung":
        "Halving of current values for better resolution",
    "Anzahl Zuendungen": "Number of ignitions",
    "Schluessel # Auto Lock Bit#": "Key # auto lock bit #",
    "UHF Empfaenger Programmierung": "UHF receiver programming",
    "UHF Zaehler": "UHF counter",
    "Komfort schlaeft": "Comfort bus asleep",
    "MMI schlaeft": "MMI asleep",
    "Antrieb schlaeft": "Drivetrain bus asleep",
    "Fahrwerk schlaeft": "Chassis bus asleep",
    "LIN schlaeft": "LIN asleep",
    "Zeitstempel WUG ein": "Timestamp WUG on",
    "Zeitstempel WUG aus": "Timestamp WUG off",
    "Verbrauchsmassnahmen VM Aoff Start-Stop":
        "Consumption measures VM Aoff, start-stop",
    "Verbrauchsmassnahmen VM Aoff Segeln":
        "Consumption measures VM Aoff, coasting",
    "Verbrauchsmassnahmen VM Aoff Schaltpunkte ECO":
        "Consumption measures VM Aoff, shift points ECO",
    "Verbrauchsmassnahmen VM SPORT Start-Stop":
        "Consumption measures VM SPORT, start-stop",
    "Verbrauchsmassnahmen VM SPORT Segeln":
        "Consumption measures VM SPORT, coasting",
    "Verbrauchsmassnahmen VM SPORT Schaltpunkte ECO":
        "Consumption measures VM SPORT, shift points ECO",
    "SIA Ruecksetzdaten": "SIA reset data",
    "Haendlernummer": "Dealer number",
    "Funktionszustaende": "Function states",
    "Radposition der ID": "Wheel position of the ID",
    "Zuletzt empfangene RAD-ID": "Most recently received wheel ID",
    "Zuendkreiskodierung # Reserviert fuer zusaetzliche Zuendpille #":
        "Ignition circuit coding #, reserved for additional squib #",
    "Messwert reservierter Zuendkreis #":
        "Measured value, reserved ignition circuit #",
    "LIN Luftguete Sensor": "LIN air quality sensor",
    "Intervallton # hinten": "Interval tone # rear",
    "Intervallton # vorne": "Interval tone # front",
    "Hoehenwerte von PASM": "Height values from PASM",
    "Entwicklermenue": "Developer menu",
    "Kartendaten Navi Suedafrika": "Map data navi South Africa",
    "Kartendaten Navi Suedamerika": "Map data navi South America",
    "min. Betaetigungszeit Schluesseltaste #":
        "Min. actuation time, key button #",
    "gefilterter Taste # Status": "Filtered button # status",
    "EZS Schluessel Position": "EZS key position",
    "EVLS Tasterbetaetigung": "EVLS button actuation",
    "Scheinwerfer links": "Headlight left",
    "Scheinwerfer rechts": "Headlight right",
    "Verstaerker": "Amplifier",
    "Batterie-#D-Code": "Battery #D code",
    "Dauer der letzten Fahrt [h]": "Duration of the last trip [h]",
    "Grund des Eintrags [#=Fremdladung, #=falsche Fremdladung, "
    "#=Batterieaberkennung, #=Zeit-Datums-Aenderung]":
        "Reason for entry [#=external charging, #=incorrect external "
        "charging, #=battery rejection, #=time/date change]",
    "Zeit-Datumsaenderungsnummer": "Time/date change number",
    "Datum/Zeit vor Aenderung": "Date/time before change",
    "Batteriespannung Kl#-aus": "Battery voltage Kl#-off",
    "Batteriespannung Kl#-ein": "Battery voltage Kl#-on",
    "Dauer Klemme-S ein": "Duration terminal S on",
    "Letztes aktives BC-Menue": "Last active BC menu",
    "Dimmung Ambiente Licht": "Dimming ambient light",
    "Dimmung": "Dimming",
    "Verbrauchsguenstiger Kennfeldbereich Drehzahl":
        "Fuel-efficient map range, engine speed",
    "Verbrauchsguenstiger Kennfeldbereich Y# Min":
        "Fuel-efficient map range Y# min",
    "Verbrauchsguenstiger Kennfeldbereich Y# Max":
        "Fuel-efficient map range Y# max",
    # --- segments common in histograms / loggers / extended fault data ---
    "Histogramm Nr. #": "Histogram no. #",
    "Tanklogger": "Tank logger",
    "Nachtankereignis #": "Refueling event #",
    "Warnungslogger": "Warning logger",
    "Adaption Geberrad Zylinder #": "Adaptation sender wheel, cylinder #",
    "Adaption Klopfregelung Zylinder #":
        "Adaptation knock control, cylinder #",
    "Adaption Neutralgangsensor": "Adaptation neutral gear sensor",
    "Adaptionsdaten Block #": "Adaptation data block #",
    "Aktive Warnungen als Liste": "Active warnings as list",
    "Aktive Warnungen als Bitfeld": "Active warnings as bit field",
    "Warnungsidentifikation": "Warning identification",
    "Warnungskonfiguration": "Warning configuration",
    "Schlafbereitschaft": "Sleep readiness",
    "HIST Abschaltstufenchronik": "HIST shutdown stage history",
    "Abschaltstufen_Eintrag_#": "Shutdown stage entry_#",
    "Umgebungswerte": "Ambient values",
    "Umgebungsdaten": "Ambient data",
    "RBM Daten #": "RBM data #",
    "Eintrag #": "Entry #",
    "Eintrag_#": "Entry_#",
    "Codierung": "Coding",
    "Tank-Variante #": "Tank variant #",
    "Hochschaltempfehlung": "Upshift recommendation",
    "Parametrierung Kurvenlicht (Algorithmus)":
        "Parameterization cornering light (algorithm)",
    "Parametrierung Kurvenlicht (Schrittmotor)":
        "Parameterization cornering light (stepper motor)",
    "Parametrierung Autobahnlicht (Algorithmus)":
        "Parameterization highway light (algorithm)",
    "Parametrierung Stadtlicht (Algorithmus)":
        "Parameterization city light (algorithm)",
    "Parametrierung ALWR (Algorithmus)":
        "Parameterization ALWR (algorithm)",
    "Parametrierung ALWR (Schrittmotor)":
        "Parameterization ALWR (stepper motor)",
    "Parametrierung AFS / VLV (Schrittmotor)":
        "Parameterization AFS / VLV (stepper motor)",
    "Parametrierung dyn. Haltestrom DKL (Schrittmotor)":
        "Parameterization dyn. holding current DKL (stepper motor)",
    "Parametrierung Fahrzeug": "Parameterization vehicle",
    "Kennfeld": "Map",
    "Gang #": "Gear #",
    "Tag": "Day",
    "Monat": "Month",
    "Jahr": "Year",
    "Stunde": "Hour",
    "HIST Energiebilanz Standphase": "HIST energy balance, idle phase",
    "Energiebilanz Standphase_#": "Energy balance idle phase_#",
    "HIST Energiebilanz Fahrt": "HIST energy balance, trip",
    "Energiebilanz Fahrt_#": "Energy balance trip_#",
    "HIST Gesamtenergiebilanz": "HIST total energy balance",
    "Vorindikator #": "Pre-indicator #",
    "Istverbauliste": "Actual installation list",
    "Sollverbauliste": "Target installation list",
    "Entwicklungsschnittstellen": "Development interfaces",
    "Programmierdatum": "Programming date",
    "Aussenbeleuchtungsparameter": "Exterior lighting parameters",
    "History Fehlerspeicher": "History fault memory",
    "ForceSleep Historiendaten": "ForceSleep history data",
    "Prevent Wakeup Historiendaten": "Prevent wakeup history data",
    "Chronik Start/Stop": "History start/stop",
    "Bitfeld #": "Bit field #",
    "CAN-Signale #": "CAN signals #",
    "CAN-Signale #/CAN-Status": "CAN signals #/CAN status",
    "Katalysator": "Catalytic converter",
    "Entnehmbare Ladung Qe (Ah)": "Extractable charge Qe (Ah)",
    "Kennlinien Nacht": "Characteristic curves, night",
    "Kennlinien Tag": "Characteristic curves, day",
    "RDK-Solldruckpaare": "RDK target pressure pairs",
    "HIST Fahrzeugliegenbleiberchronik":
        "HIST vehicle breakdown history",
    "Fahrzeugliegenbleiberchronik_#": "Vehicle breakdown history_#",
    "Tankdaten": "Tank data",
    "GQS Daten": "GQS data",
    "CAN-Bits": "CAN bits",
    "Druckkorrekturparameter": "Pressure correction parameters",
    "Start/Stopp Ereignisspeicher": "Start/stop event memory",
    "Historienspeicher ZV": "History memory, central locking",
    "Historienspeicher DWA": "History memory, anti-theft alarm",
    "HIST Batteriealterung": "HIST battery aging",
    "Batteriealterung_#": "Battery aging_#",
    "Gemischbildung": "Mixture formation",
    "Kilometerstand": "Odometer reading",
    "Kodierwert": "Coding value",
    "Kodierbyte #": "Coding byte #",
    "HIST Batteriewechselchronik#": "HIST battery replacement history #",
    "Batteriewechselchronik_#": "Battery replacement history_#",
    "Batterienummer": "Battery number",
    "freigeschaltet": "enabled",
    "Autom.Test": "Autom. test",
    "Leuchtdichte OUT": "Luminance OUT",
    "Diagnosebereitschaft": "Diagnostic readiness",
    "HIST Stop verbieten": "HIST prohibit stop",
    "Stop verbieten": "Prohibit stop",
    "manuell quittiert": "manually acknowledged",
    "in Warnereignisspeicher": "in warning event memory",
    "Allgemein": "General",
    "Standard Software Komponenten MOST":
        "Standard software components MOST",
    "Spoiler-Parameter": "Spoiler parameters",
    "Deaktivierungsschalter/PSBR": "Deactivation switch/PSBR",
    "Fahrerairbag #. Stufe": "Driver airbag, stage #",
    "Beifahrerairbag #. Stufe": "Passenger airbag, stage #",
    "ApplikationSoftwareDatum": "Application software date",
    "Weitere Codierungen": "Further codings",
    "Schluesselparameter": "Key parameters",
    "TesterdatumKodierung Lesen": "Tester date coding, read",
    "Testerdatum Schreiben": "Tester date, write",
    "Status WFS-Slaves": "Status WFS (immobilizer) slaves",
    "Schalter Taster": "Switches and buttons",
    "V# Anfang": "V# start",
    "V# Ende": "V# end",
    "V# Farbe": "V# color",
    "Status Motornormierung": "Status motor normalization",
    "LIN Motorsteuerung #": "LIN motor control #",
    "Stecker C": "Connector C",
    "HIST Leerlaufanhebungschronik":
        "HIST idle-speed increase history",
    "Leerlaufanhebungschronik_#": "Idle-speed increase history_#",
    "Warnlampen": "Warning lamps",
    "Grundinformation": "Basic information",
    "SW-Versionen Liste #": "SW versions list #",
    "Bitwerte #": "Bit values #",
    "allgemeine Konfiguration": "general configuration",
    "Berechnete, angezeigte Werte": "Calculated, displayed values",
    "Zuziehhilfe": "Soft-close assist",
    "Crashout/Lampen": "Crashout/lamps",
    "CAN-Ausgabe": "CAN output",
    "Nockenwellenverstellung": "Camshaft adjustment",
    "Zustand KlG#": "State KlG#",
    "VZA-Konfiguration": "VZA configuration",
    "Motordrehzahl": "Engine speed",
    "CAN Eingangssignale": "CAN input signals",
    "verbaut": "installed",
    "funktionsbereit": "operational",
    "EEPROM Datensatz Info": "EEPROM data record info",
    "Prozenteinstellung IN": "Percent setting IN",
    "ApplikationDatensatzNummer": "Application data record number",
    "Allgemein-Parameter": "General parameters",
    "Mehrausstattung": "Optional equipment",
    "Mehrausstattungen": "Optional equipment",
    "Klemmensteuerung": "Terminal control",
    "Seriennummer": "Serial number",
    "digitale Stati": "Digital states",
    "Beleuchtung": "Lighting",
    "Uhrzeit #": "Time #",
    "Funktionscode #": "Function code #",
    "Memory IDs / Funk": "Memory IDs / radio",
    "Gurtschloss-Statusinformation": "Belt buckle status information",
    "Diagnosestatus Gurtschloss": "Diagnostic status, belt buckle",
    "Drehzahl letztes Auftreten": "Engine speed, last occurrence",
    "Drehzahl erstes Auftreten": "Engine speed, first occurrence",
    "Fahrzeuggeschwindigkeit": "Vehicle speed",
    "Geschwindigkeit letztes Auftreten": "Speed, last occurrence",
    "Geschwindigkeit erstes Auftreten": "Speed, first occurrence",
    "Innentemperatur": "Interior temperature",
    "Kuehlmiteltemperatur letztes Auftreten":
        "Coolant temperature, last occurrence",
    "Kuehlmitteltemperatur erstes Auftreten":
        "Coolant temperature, first occurrence",
    "Sonnenintensitaet": "Sun intensity",
    "StandardFreezeFrame letztes Auftreten":
        "StandardFreezeFrame, last occurrence",
    "interne Fehlerart": "Internal fault type",
    "interner Fehlercode": "Internal fault code",
    "Oeltemperatur erstes Auftreten": "Oil temperature, first occurrence",
    "Oeltemperatur letztes Auftreten": "Oil temperature, last occurrence",
    "Zeit seit Motorstart": "Time since engine start",
    "Bedienelement": "Operating element",
    "Betriebszustand": "Operating state",
    "Fahrzeugzustand": "Vehicle state",
    "Status Fahrzeuggeschwindigkeit": "Status vehicle speed",
    "Bremspedal": "Brake pedal",
    "Kupplungspedal": "Clutch pedal",
    "Status Aktuator Position links": "Status actuator position, left",
    "Status Aktuator Position rechts": "Status actuator position, right",
    "Aktuator-Verfuegbarkeit": "Actuator availability",
    "Anforderung Notbremsfunktion": "Request emergency brake function",
    "TUeV-Modus": "TUEV (inspection) mode",
    "Betriebsdauer": "Operating time",
    "Betriebsspannung": "Operating voltage",
    "Berechnete Fahrstufe": "Calculated gear",
    "Ereigniskategorie": "Event category",
    "Fehlerstatus der Hall-Sensoren": "Fault status of the Hall sensors",
    "Gesamtwegstreckenzaehler": "Total distance counter",
    "Hinweis_Prio": "Note_priority",
    "Zaehler Freischaltversuche": "Counter of unlock attempts",
    "Anzeige der der angelernten Komponenten":
        "Display of the learned components",
}

# --- BC configuration byte suffixes share one prefix; generated below ------
_BC_PREFIX = "BC-Konfiguration Byte # "
_BC_SUFFIXES = {
    "Status oben": "status top",
    "Status unten": "status bottom",
    "Auto-MEM": "Auto-MEM",
    "Anzeige Speed Limit Navi": "display speed limit navi",
    "Tooltip Active": "Tooltip Active",
    "BC-Rolle Audio": "BC role audio",
    "BC-Rolle Navi": "BC role navi",
    "BC-Rolle Map": "BC role map",
    "BC-Rolle Telefon": "BC role phone",
    "BC-Rolle Allrad": "BC role all-wheel drive",
    "BC-Rolle Hybrid": "BC role hybrid",
    "BC-Rolle ACC": "BC role ACC",
    "BC-Rolle G-Force": "BC role G-force",
    "BC-Rolle Hochschaltanzeige": "BC role upshift display",
    "BC-Rolle VZA": "BC role VZA (traffic sign display)",
    "BC-Rolle Trip": "BC role trip",
    "BC-Rolle RDK": "BC role RDK (tire pressure)",
    "BC-Rolle Chrono": "BC role chrono",
    "BC-Rolle Performance-Anzeige": "BC role performance display",
    "Kartenanzeige": "map display",
    "Kartenorientierung": "map orientation",
    "PCM Anzeigen Kreuzungszoom": "PCM displays intersection zoom",
    "PCM Anzeigen Abbiegehinweise": "PCM displays turn instructions",
    "PCM Anzeigen Telefon-Info": "PCM displays phone info",
    "PCM Anzeigen Sprachbedien-Info": "PCM displays voice control info",
    "Eco-Hochschaltanzeige": "eco upshift display",
    "Aussenlicht Nachleuchtzeit": "exterior light afterglow time",
    "Innenlicht Orientierungslicht": "interior light orientation light",
    "Innenlicht Nachleuchtzeit": "interior light afterglow time",
    "Innenlicht an, wenn Tuerkontakt offen":
        "interior light on when door contact open",
    "Wischer Regensensor": "wiper rain sensor",
    "Wischer Heckwischer": "wiper rear wiper",
    "Rueckfahroptionen Spiegel Absenken":
        "reversing options mirror tilt-down",
    "Rueckfahroptionen Heckrollo oeffnen":
        "reversing options rear blind open",
    "Einstellung Tuerentriegelung": "setting door unlocking",
    "Einstellung Tuerverriegelung (CarJack)":
        "setting door locking (CarJack)",
    "Einstellung Klimastil": "setting climate style",
    "Verriegelung Spiegel Einklappen": "locking mirror fold-in",
    "Verriegelung Einstiegshilfe": "locking entry aid",
    "Verriegelung Wiederverriegelungszeit, Nach Tuerentriegelung":
        "locking re-lock time after door unlocking",
    "Verriegelung Wiederverriegelungszeit, Nach Kofferraum oeffnen":
        "locking re-lock time after trunk opening",
    "Klima Temperaturabsenkung Mittelduese":
        "climate temperature reduction, center vent",
    "Klima Erweitertes Belueftungsfeld":
        "climate extended ventilation field",
    "Klima Auto-Umluft": "climate auto recirculation",
    "Uhrzeitformat": "time format",
    "Datumsformat": "date format",
    "HAL_Schneekette": "HAL_snow chain",
    "GPS-Uhrzeit": "GPS time",
    "Performance-Anzeige": "performance display",
    "FLA/GLW-Funktion": "FLA/GLW function",
    "Anzeige Uhrzeit in Stoppuhr": "display time in stopwatch",
    "Einheit Tacho": "unit speedometer",
    "Einheit Temperatur": "unit temperature",
    "Einheit Reifendruck": "unit tire pressure",
    "Einheit Ladedruck": "unit boost pressure",
    "Einheit Oeldruck": "unit oil pressure",
    "Einheit Verbrauch": "unit consumption",
    "Sprache": "language",
    "Verkehrszeichen oben/rechts": "traffic signs top/right",
    "Bildschirm Helligkeit": "screen brightness",
    "Warntoene Parkassistent": "warning tones park assist",
    "Warntoene Systemtoene": "warning tones system tones",
    "Lenkrad Multifunktionstaste": "steering wheel multifunction button",
    "Lenkrad-Schaltanzeige (Schaltblitz)":
        "steering wheel shift display (shift flash)",
    "Autozoom": "autozoom",
    "Selected Trip Computer": "Selected Trip Computer",
    "Preset Liste": "preset list",
    "und # Fahrerdimmungswert": "and #, driver dimming value",
    "und # Car Connect Privacy": "and #, Car Connect Privacy",
}
for _de, _en in _BC_SUFFIXES.items():
    TRANS_SEGMENT[_BC_PREFIX + _de] = "BC configuration byte #, " + _en

# --- word/phrase fallback (lowercase keys, whole-word, longest first) ------
TRANS_PHRASE = {
    "und": "and", "oder": "or", "nicht": "not", "kein": "no", "keine": "no",
    "keiner": "none", "mit": "with", "ohne": "without", "fuer": "for",
    "von": "from", "vor": "before", "nach": "after", "bei": "at",
    "seit": "since", "zwischen": "between", "wegen": "due to",
    "durch": "by", "ueber": "via", "innerhalb": "within",
    "anzahl der": "number of", "als": "as", "beim": "at",
    "einmal": "once",
    "ausserhalb": "outside", "oberhalb": "above", "unterhalb": "below",
    "im": "in", "zum": "for", "zur": "for", "der": "of the",
    "des": "of the", "die": "the", "das": "the", "dem": "the",
    "den": "the", "ist": "is", "sind": "are", "war": "was", "wird": "is",
    "wenn": "when", "weil": "because", "nur": "only", "alle": "all",
    "beide": "both", "eine": "one", "ein": "on", "aus": "off",
    "aktuelle": "current", "aktuellen": "current", "aktueller": "current",
    "letzte": "last", "letzter": "last", "letzten": "last",
    "letztes": "last", "erste": "first", "erster": "first",
    "erstes": "first", "erstem": "first", "neu": "new", "alt": "old",
    "weitere": "further", "zuletzt": "most recently",
    "mindestens": "at least", "moeglich": "possible", "gleich": "equal",
    "manuell": "manually", "automatisch": "automatically",
    "links": "left", "rechts": "right", "oben": "top", "unten": "bottom",
    "vorne": "front", "vorn": "front", "hinten": "rear", "mitte": "center",
    "innen": "inside", "aussen": "outside", "hoch": "up",
    "vorwaerts": "forward", "rueckwaerts": "backward",
    "fahrerseite": "driver side", "beifahrerseite": "passenger side",
    "fahrer": "driver", "beifahrer": "passenger",
    "fond": "rear compartment",
    "lesen": "read", "gelesen": "read", "schreiben": "write",
    "loeschen": "clear", "geloescht": "cleared", "betaetigt": "pressed",
    "unbetaetigt": "not pressed", "gedrueckt": "pressed",
    "erkannt": "detected", "erfuellt": "fulfilled",
    "durchgefuehrt": "performed", "gesendet": "sent",
    "empfangen": "received", "empfangene": "received",
    "gespeichert": "stored", "gesp.": "stored", "angelernt": "learned",
    "angelernten": "learned", "unverbaut": "not installed",
    "aktiviert": "activated", "deaktiviert": "deactivated",
    "aktivieren": "activate", "deaktivierung": "deactivation",
    "aktivierung": "activation", "freigegeben": "released",
    "freigegebene": "released", "freigegebenen": "released",
    "gestoert": "disturbed", "fehlerhaft": "faulty", "gueltig": "valid",
    "ungueltig": "invalid", "zulaessig": "permissible",
    "zulaessigen": "permissible", "erlaubt": "permitted",
    "verbieten": "prohibit", "anfordern": "request",
    "geoeffnet": "opened", "geschlossen": "closed", "offen": "open",
    "oeffnen": "open", "schliessen": "close", "sperren": "lock",
    "entriegelt": "unlocked", "verriegelt": "locked",
    "einschalten": "switch-on", "abschalten": "switch-off",
    "eingeschaltet": "switched on", "ausgeschaltet": "switched off",
    "abgeschaltet": "switched off", "abgelaufen": "expired",
    "erfolgt": "done", "erfolgreich": "successful",
    "fehlgeschlagen": "failed", "laeuft": "running", "steht": "stopped",
    "schlaeft": "asleep", "wartet": "waiting", "pruefen": "check",
    "geprueft": "checked", "kalibriert": "calibrated",
    "normiert": "normalized", "invertiert": "inverted",
    "qualifiziert": "qualified", "quittiert": "acknowledged",
    "quittierbar": "acknowledgeable", "berechnete": "calculated",
    "berechnet": "calculated", "angezeigte": "displayed",
    "angezeigt": "displayed", "verfuegbar": "available",
    "vorhanden": "present", "belegt": "assigned", "aktiv": "active",
    "inaktiv": "inactive", "defekt": "defective", "leer": "empty",
    "voll": "full", "konsistent": "consistent",
    "fehler": "fault", "fehlers": "fault", "warnung": "warning",
    "warnungen": "warnings", "schluessel": "key", "tuer": "door",
    "tueren": "doors", "sitz": "seat", "rad": "wheel",
    "raeder": "wheels", "bremse": "brake", "batterie": "battery",
    "spannung": "voltage", "strom": "current", "druck": "pressure",
    "temperatur": "temperature", "geschwindigkeit": "speed",
    "drehzahl": "engine speed", "zaehler": "counter",
    "anzahl": "number of", "wert": "value", "werte": "values",
    "messwert": "measured value", "messwerte": "measured values",
    "sollwert": "target value", "istwert": "actual value",
    "sollposition": "target position", "istposition": "actual position",
    "kennlinie": "characteristic curve",
    "kennlinien": "characteristic curves", "kennfeld": "map",
    "schwelle": "threshold", "schwellen": "thresholds",
    "grenzwert": "limit value", "bereich": "range",
    "bereiches": "range", "zustand": "state", "zustaende": "states",
    "zeit": "time", "datum": "date", "uhrzeit": "time",
    "dauer": "duration", "zyklus": "cycle", "zyklen": "cycles",
    "kilometerstand": "odometer reading", "teil": "part",
    "grund": "reason", "gruende": "reasons", "eintrag": "entry",
    "eintraege": "entries", "eintrags": "entry", "liste": "list",
    "chronik": "history", "historie": "history",
    "speicher": "memory", "fehlerspeicher": "fault memory",
    "ereignis": "event", "ereignisse": "events",
    "ausloesung": "deployment", "ausloesungen": "deployments",
    "ansteuerung": "activation", "steuerung": "control",
    "regelung": "control", "ueberwachung": "monitoring",
    "diagnose": "diagnosis", "pruefung": "test", "pruefungen": "tests",
    "bedingung": "condition", "bedingungen": "conditions",
    "funktion": "function", "funktionen": "functions",
    "konfiguration": "configuration", "einstellung": "setting",
    "einstellungen": "settings", "parametrierung": "parameterization",
    "programmierung": "programming", "kodierung": "coding",
    "codierung": "coding", "codierungen": "codings",
    "kodierwert": "coding value", "kodierwerte": "coding values",
    "codierwert": "coding value", "variante": "variant",
    "varianten": "variants", "ausstattung": "equipment",
    "sensoren": "sensors", "signale": "signals",
    "botschaft": "message", "botschaftsfehler": "message fault",
    "klemme": "terminal", "sicherung": "fuse", "relais": "relay",
    "lampe": "lamp", "leuchte": "lamp", "licht": "light",
    "beleuchtung": "lighting", "helligkeit": "brightness",
    "blinker": "turn signal", "scheinwerfer": "headlight",
    "fernlicht": "high beam", "abblendlicht": "low beam",
    "standlicht": "standing light", "nebellicht": "fog light",
    "innenlicht": "interior light", "aussenlicht": "exterior light",
    "tagfahrlicht": "daytime running light", "umluft": "recirculation",
    "geblaese": "blower", "innengeblaese": "interior blower",
    "klima": "climate", "heizung": "heating", "lueftung": "ventilation",
    "mittelduese": "center vent", "spiegel": "mirror",
    "aussenspiegel": "exterior mirror", "wischer": "wiper",
    "heckwischer": "rear wiper", "wischwinkel": "wiper angle",
    "regensensor": "rain sensor", "drucksensor": "pressure sensor",
    "beschleunigungssensor": "acceleration sensor",
    "sensorwert": "sensor value", "sensorwerte": "sensor values",
    "gurt": "belt", "gurtstraffer": "belt tensioner",
    "gurtschloss": "belt buckle", "gurtschloesser": "belt buckles",
    "gurtwarnung": "belt warning", "insassenerkennung":
        "occupant detection", "fussgaengerschutz": "pedestrian protection",
    "ueberrollschutz": "rollover protection",
    "lenksaeule": "steering column", "lenkrad": "steering wheel",
    "lenkunterstuetzung": "steering assist", "lenkung": "steering",
    "fahrwerk": "chassis", "daempfer": "damper",
    "daempferstrom": "damper current", "vorderachse": "front axle",
    "hinterachse": "rear axle", "achse": "axis", "reifen": "tire",
    "reifendruck": "tire pressure", "reifentyp": "tire type",
    "reifenumfang": "tire circumference", "tankinhalt": "tank content",
    "tankanzeige": "fuel gauge", "tankgeber": "tank sender",
    "kraftstoff": "fuel", "verbrauch": "consumption",
    "reichweite": "range", "restreichweite": "remaining range",
    "oeldruck": "oil pressure", "oelstand": "oil level",
    "oeltemperatur": "oil temperature", "kuehlmittel": "coolant",
    "motoroel": "engine oil", "ladedruck": "boost pressure",
    "abgas": "exhaust", "katalysator": "catalytic converter",
    "lambdasonde": "oxygen sensor", "zuendung": "ignition",
    "zuendungen": "ignitions", "zuendkreis": "ignition circuit",
    "zuendpille": "squib", "zuendwinkel": "ignition angle",
    "klopfregelung": "knock control", "nockenwelle": "camshaft",
    "zylinder": "cylinder", "einspritzung": "injection",
    "leerlauf": "idle", "drosselklappe": "throttle valve",
    "kupplung": "clutch", "gang": "gear", "gaenge": "gears",
    "ganganzeige": "gear display", "schaltung": "shift",
    "schaltpunkte": "shift points", "waehlhebel": "selector lever",
    "fahrstufe": "gear", "allrad": "all-wheel drive",
    "quersperre": "differential lock", "motorstart": "engine start",
    "steuergeraet": "control unit", "steuergeraete": "control units",
    "steuergeraets": "control unit", "verdeck": "convertible top",
    "dach": "roof", "deckel": "lid", "heckklappe": "tailgate",
    "kofferraum": "trunk", "handschuhkasten": "glovebox",
    "fensterheber": "window lifter", "zentralverriegelung":
        "central locking", "verriegelung": "locking",
    "entriegelung": "unlocking", "tuerentriegelung": "door unlocking",
    "tuerverriegelung": "door locking", "tuerkontakt": "door contact",
    "einstiegshilfe": "entry aid", "zuziehhilfe": "soft-close assist",
    "diebstahlwarnanlage": "anti-theft alarm",
    "wegfahrsperre": "immobilizer", "funkschluessel": "radio key",
    "heckrollo": "rear blind", "sonnenrollo": "sun blind",
    "dachkonsole": "roof console", "anhaenger": "trailer",
    "fahrzeug": "vehicle", "fahrzeuge": "vehicles",
    "fahrzyklus": "driving cycle", "fahrt": "trip",
    "wegstrecke": "distance", "stillstand": "standstill",
    "kurve": "curve", "kurven": "curves", "kurvenlicht":
        "cornering light", "kurvenradius": "curve radius",
    "autobahnlicht": "highway light", "stadtlicht": "city light",
    "crashdaten": "crash data", "crashereignis": "crash event",
    "crashzaehler": "crash counter", "zeitstempel": "timestamp",
    "warnlampe": "warning lamp", "warnlampen": "warning lamps",
    "warntoene": "warning tones", "hinweis": "note",
    "anzeige": "display", "anzeigen": "displays",
    "analoganzeigen": "analog gauges", "bordcomputer":
        "on-board computer", "kombiinstrument": "instrument cluster",
    "tacho": "speedometer", "stoppuhr": "stopwatch",
    "sprache": "language", "einheit": "unit", "einheiten": "units",
    "taste": "button", "tasten": "buttons", "taster": "button",
    "tastenfeld": "keypad", "schalter": "switch",
    "betaetigung": "actuation", "betaetigungszeit": "actuation time",
    "verstellung": "adjustment", "verstellungen": "adjustments",
    "verstellweg": "adjustment travel", "hoehenverstellung":
        "height adjustment", "lordose": "lumbar",
    "lordosenverstellung": "lumbar adjustment",
    "sitzheizung": "seat heating", "kopfstuetze": "headrest",
    "armlehne": "armrest", "airbag": "airbag",
    "fahrerairbag": "driver airbag", "beifahrerairbag":
        "passenger airbag", "knieairbag": "knee airbag",
    "thoraxairbag": "thorax airbag", "seitenairbag": "side airbag",
    "kopfairbag": "head airbag", "generator": "alternator",
    "generatorfehler": "alternator fault", "anlasser": "starter",
    "starterrelais": "starter relay", "bordspannung":
        "on-board voltage", "bordnetz": "electrical system",
    "ruhestrom": "quiescent current", "ruhespannung":
        "quiescent voltage", "versorgungsspannung": "supply voltage",
    "batteriespannung": "battery voltage", "unterspannung":
        "undervoltage", "ueberspannung": "overvoltage",
    "spannungsversorgung": "power supply", "stromversorgung":
        "power supply", "kurzschluss": "short circuit",
    "unterbrechung": "open circuit", "masseschluss": "short to ground",
    "plusschluss": "short to plus", "leitungsbruch": "broken wire",
    "widerstand": "resistance", "widerstaende": "resistances",
    "kapazitaet": "capacity", "ladung": "charge",
    "entladung": "discharge", "fremdladung": "external charging",
    "energiebilanz": "energy balance", "abschaltung": "switch-off",
    "einschaltzeit": "switch-on time", "ausschaltzeit":
        "switch-off time", "nachleuchtzeit": "afterglow time",
    "verzoegerungszeit": "delay time", "rampenzeit": "ramp time",
    "wartezeit": "wait time", "laufzeit": "run time",
    "gesamtdauer": "total duration", "restlaufzeit": "remaining time",
    "histogramm": "histogram", "nr.": "no.", "stufe": "stage",
    "stufen": "stages", "position": "position", "positionen":
        "positions", "richtung": "direction", "winkel": "angle",
    "hebel": "lever", "pedal": "pedal", "bremspedal": "brake pedal",
    "kupplungspedal": "clutch pedal", "gaspedal": "accelerator pedal",
    "erkennung": "detection", "abgleich": "calibration",
    "kalibrierung": "calibration", "normierung": "normalization",
    "initialisierung": "initialization", "adaption": "adaptation",
    "anpassung": "adaptation", "geber": "sender", "geberrad":
        "sender wheel", "istwerte": "actual values",
    "sollwerte": "target values", "rohwert": "raw value",
    "mittelwert": "mean value", "maximalwert": "maximum value",
    "minimalwert": "minimum value", "endwert": "final value",
    "startwert": "start value", "zaehlerstand": "counter reading",
    "startversuche": "start attempts", "versuche": "attempts",
    "auftreten": "occurrence", "haeufigkeit": "frequency",
    "wiederholung": "repetition", "intervall": "interval",
    "periode": "period", "frequenz": "frequency", "phase": "phase",
    "amplitude": "amplitude", "pegel": "level", "stand": "level",
    "fuellstand": "fill level", "niveau": "level", "hoehe": "height",
    "hoehen": "heights", "hoehenwert": "height value",
    "hoehenwerte": "height values", "breite": "width",
    "laenge": "length", "abstand": "distance", "luftdruck":
        "air pressure", "luftguete": "air quality",
    "aussentemperatur": "outside temperature", "innentemperatur":
        "interior temperature", "umgebungstemperatur":
        "ambient temperature", "verdampfer": "evaporator",
    "kondensator": "condenser", "kompressor": "compressor",
    "kaeltemittel": "refrigerant", "ausblastemperatur":
        "outlet temperature", "temperaturabsenkung":
        "temperature reduction", "sonnenintensitaet": "sun intensity",
    "beleuchtungsstaerke": "illuminance", "leuchtdichte": "luminance",
    "dimmung": "dimming", "daemmerung": "twilight",
    "nacht": "night", "tag": "day", "monat": "month", "jahr": "year",
    "stunde": "hour", "minute": "minute", "sekunde": "second",
    "woche": "week", "heute": "today", "russland": "Russia",
    "suedafrika": "South Africa", "suedamerika": "South America",
    "nordamerika": "North America", "europa": "Europe",
    "kanada": "Canada", "mexiko": "Mexico", "brasilien": "Brazil",
    "australien": "Australia", "japan": "Japan", "china": "China",
    "tuerkei": "Turkey", "naher osten": "Middle East",
    "deutschland": "Germany", "frankreich": "France",
    "oesterreich": "Austria", "schweiz": "Switzerland",
    "spanien": "Spain", "italien": "Italy", "arabien": "Arabia",
    "kartendaten": "map data", "karte": "map", "navi": "navi",
    "telefon": "phone", "verkehrszeichen": "traffic signs",
    "zusatzschilder": "supplementary signs", "schilder": "signs",
    "einparkhilfe": "park assist", "parkassistent": "park assist",
    "rueckfahrkamera": "reversing camera", "rueckwaertsgang":
        "reverse gear", "standheizung": "auxiliary heating",
    "zuheizer": "auxiliary heater", "segeln": "coasting",
    "rekuperation": "recuperation", "verstaerker": "amplifier",
    "lautsprecher": "speaker", "mikrofon": "microphone",
    "antenne": "antenna", "empfaenger": "receiver", "sender": "sender",
    "wachhaltegruende": "keep-awake reasons", "aufwachgrund":
        "wake-up reason", "schlafbereitschaft": "sleep readiness",
    "schlafbereiter": "ready-to-sleep", "schlafbereit":
        "ready to sleep", "komfort": "comfort", "antrieb": "drivetrain",
    "eingangssignale": "input signals", "ausgangssignale":
        "output signals", "eingaenge": "inputs", "ausgaenge": "outputs",
    "eingang": "input", "ausgang": "output", "peripherie":
        "peripherals", "schnittstelle": "interface",
    "schnittstellen": "interfaces", "protokoll": "protocol",
    "datensatz": "data record", "datensaetze": "data records",
    "daten": "data", "seriennummer": "serial number",
    "teilenummer": "part number", "fahrgestellnummer": "VIN",
    "pruefsumme": "checksum", "kennung": "identifier",
    "identifikation": "identification", "softwarestand": "SW version",
    "hardwarestand": "HW version", "stueckliste": "parts list",
    "fertigungsdaten": "production data", "produktionsdatum":
        "production date", "herstellernummer": "manufacturer number",
    "werkseinstellung": "factory setting", "auslieferungszustand":
        "delivery state", "transportmodus": "transport mode",
    "servicemodus": "service mode", "notlauf": "limp mode",
    "ersatzwert": "substitute value", "ersatzmassnahme":
        "substitute measure", "ersatzmassnahmen": "substitute measures",
    "einschraenkung": "restriction", "einschraenkungen":
        "restrictions", "stoerung": "malfunction", "stoerungen":
        "malfunctions", "fehlerart": "fault type", "fehlercode":
        "fault code", "fehlerursache": "fault cause", "fehlerliste":
        "fault list", "fehlerbits": "fault bits", "fehlereintrag":
        "fault entry", "fehlerstatus": "fault status",
    "fehlererkennung": "fault detection", "fehlersymptom":
        "fault symptom", "analyse": "analysis", "auswertung":
        "evaluation", "bewertung": "evaluation", "statistik":
        "statistics", "stichprobe": "sample", "referenz": "reference",
    "toleranz": "tolerance", "offset": "offset", "faktor": "factor",
    "verhaeltnis": "ratio", "prozent": "percent", "anteil": "share",
    "summe": "sum", "differenz": "difference", "mittel": "medium",
    "maximal": "maximum", "minimal": "minimum", "max.": "max.",
    "min.": "min.", "gesamt": "total", "gesamte": "total",
    "teilweise": "partial", "vollstaendig": "complete",
    "unvollstaendig": "incomplete", "unbekannt": "unknown",
    "verstellklappen": "adjustment flaps", "temperaturmischklappe":
        "temperature mixing flap", "stellmotor": "servo motor",
    "schrittmotor": "stepper motor", "stellglied": "actuator",
    "aktuator": "actuator", "ventil": "valve", "pumpe": "pump",
    "kraftstoffpumpe": "fuel pump", "wasserpumpe": "water pump",
    "luefter": "fan", "kuehler": "radiator", "kuehlung": "cooling",
    "thermostat": "thermostat", "thermomodell": "thermal model",
    "stuetzstelle": "interpolation point", "stuetzstellen":
        "interpolation points", "gierrate": "yaw rate",
    "querbeschleunigung": "lateral acceleration",
    "laengsbeschleunigung": "longitudinal acceleration",
    "beschleunigung": "acceleration", "verzoegerung": "deceleration",
    "einfederung": "compression", "ausfederung": "rebound",
    "niveauregulierung": "level control", "schwenkempfindlichkeit":
        "swivel sensitivity", "schwenkwinkel": "swivel angle",
    "knickwinkel": "kink angle", "radwinkel": "wheel angle",
    "lenkwinkel": "steering angle", "lenkradwinkel":
        "steering wheel angle", "uebersetzung": "ratio",
    "uebertragungsfaktor": "transfer factor", "daempfung": "damping",
    "steifigkeit": "stiffness", "vorspannung": "preload",
    "grundeinstellung": "basic setting", "endanschlag": "end stop",
    "anschlag": "stop", "endlage": "end position", "mittellage":
        "center position", "nulllage": "zero position",
    "betriebsbereich": "operating range", "arbeitsbereich":
        "working range", "messbereich": "measuring range",
    "anzeigebereich": "display range", "wertebereich": "value range",
    "gurtschlossstatus": "belt buckle status", "sitzbelegung":
        "seat occupancy", "sitzposition": "seat position",
    "sitzlehne": "backrest", "rueckenlehne": "backrest",
    "verlernzaehler": "unlearn counter", "haeufigkeitszaehler":
        "frequency counter", "betriebsdauerzaehler":
        "operating time counter", "lastwechselzaehler":
        "load cycle counter", "wachhaltezaehler": "keep-awake counter",
    "startzaehler": "start counter", "stoppzaehler": "stop counter",
    "reset": "reset", "neustart": "restart", "wiederholrate":
        "repeat rate", "abtastrate": "sampling rate",
    "aufloesung": "resolution", "genauigkeit": "accuracy",
    "filterwert": "filter value", "filterzeit": "filter time",
    "glaettung": "smoothing", "glaettungszeitkonstante":
        "smoothing time constant", "zeitkonstante": "time constant",
    "hysterese": "hysteresis", "totzeit": "dead time",
    "ansprechzeit": "response time", "abfallzeit": "release time",
    "anzugszeit": "pull-in time", "schrittlaenge": "step length",
    "drehrichtung": "rotation direction", "laufrichtung":
        "running direction", "fahrtrichtung": "direction of travel",
    "kraft": "force", "moment": "torque", "drehmoment": "torque",
    "leistung": "power", "energie": "energy", "arbeit": "work",
    "wirkungsgrad": "efficiency", "verlust": "loss",
    "verluste": "losses", "gewicht": "weight", "masse": "mass",
    "beladung": "load", "zuladung": "payload",
    "volllast": "full load", "teillast": "partial load",
    "leerlaufdrehzahl": "idle speed", "solldrehzahl": "target speed",
    "istdrehzahl": "actual speed", "startdrehzahl": "starting speed",
    "grenzdrehzahl": "limit speed", "geschwindigkeitsschwelle":
        "speed threshold", "beschleunigungsschwelle":
        "acceleration threshold", "temperaturschwelle":
        "temperature threshold", "spannungsschwelle":
        "voltage threshold", "stromschwelle": "current threshold",
    "druckschwelle": "pressure threshold", "warnschwelle":
        "warning threshold", "abschaltschwelle": "switch-off threshold",
    "einschaltschwelle": "switch-on threshold",
    # second round: compounds found in the coverage measurement
    "stromflussdauer": "current flow duration",
    "diagnosestatus": "diagnostic status",
    "codierbyte": "coding byte", "kodierbyte": "coding byte",
    "aussenbeleuchtung": "exterior lighting",
    "komponenten": "components", "komponente": "component",
    "aktive": "active", "haube": "hood",
    "endbeschlaggurtstraffer": "belt end-fitting tensioner",
    "gurtkraftbegrenzer": "belt force limiter",
    "solldruckpaar": "target pressure pair",
    "solldruck": "target pressure", "standzeit": "standing time",
    "menuekonfiguration": "menu configuration", "zoll": "inch",
    "volumen": "volume", "stopp": "stop",
    "tachokennlinie": "speedometer characteristic",
    "drehmomentanzeige": "torque display",
    "leistungsanzeige": "power display",
    "analoge": "analog", "digitale": "digital",
    "fahrzeugcodierung": "vehicle coding",
    "fahrersitzpositionssensor": "driver seat position sensor",
    "beifahrersitzpositionssensor": "passenger seat position sensor",
    "codierstring": "coding string", "komfortdruck": "comfort pressure",
    "kombi": "cluster", "mehrausstattungen": "optional equipment",
    "mehrausstattung": "optional equipment", "klasse": "class",
    "allgemein": "general", "allgemeine": "general",
    "allgemeinen": "general", "allgemeiner": "general",
    "batterieabtrennung": "battery disconnection",
    "fahrzeit": "driving time", "begrenzungsfilter": "limiting filter",
    "erweiterte": "extended", "erweiterter": "extended",
    "erweitertes": "extended", "bitfeld": "bit field",
    "temperaturen": "temperatures",
    "sgprogrammierstatus": "ECU programming status",
    "radelektronik": "wheel electronics", "leitung": "line",
    "kundenverhalten": "customer behavior",
    "zuendkreiskodierung": "ignition circuit coding",
    "seitenscheibenrollo": "side window blind",
    "externe": "external", "externer": "external",
    "parkbremse": "parking brake", "fahrprogramm": "driving program",
    "gurtzubringer": "belt feeder",
    "pneumatikparameter": "pneumatic parameters",
    "softwareblock": "software block", "hardwareblock": "hardware block",
    "deaktivierungsschalter": "deactivation switch",
    "aussetzererkennung": "misfire detection",
    "maximale": "maximum", "maximaler": "maximum",
    "minimale": "minimum", "minimaler": "minimum",
    "rollo": "blind", "stellelemente": "actuators",
    "stellelement": "actuator", "kanal": "channel",
    "rippelqualitaet": "ripple quality", "algorithmus": "algorithm",
    "kurztest": "short test", "elektrische": "electric",
    "elektrisch": "electric", "elektrischer": "electric",
    "variantencodierung": "variant coding",
    "mittelkonsole": "center console", "steckdosen": "power outlets",
    "steckdose": "power outlet", "schaltzeit": "shift time",
    "kennfeldbereich": "map range",
    "getriebeposition": "transmission position",
    "kodiercontainer": "coding container",
    "schrittmotorverfahrgeschwindigkeit": "stepper motor travel speed",
    "fehlerindex": "fault index",
    "neutralgangsensor": "neutral gear sensor", "bilanz": "balance",
    "kontrollleuchten": "indicator lamps",
    "kontrollleuchte": "indicator lamp",
    "warnungsidentifikator": "warning identifier",
    "warnungszustand": "warning state",
    "testerkennung": "tester identification",
    "porscheteilenummer": "Porsche part number",
    "vorbedingungen": "preconditions", "vorbedingung": "precondition",
    "sitzbelegungserkennung": "seat occupancy detection",
    "klemmensteuerungs-parameter": "terminal control parameters",
    "aktivierbar": "activatable", "abschaltstufe": "shutdown stage",
    "standheizungsabschaltung": "auxiliary heating switch-off",
    "batteriestom": "battery current", "batteriestrom":
        "battery current", "batteriesaeure-temp": "battery acid temp",
    "applikationsoftwareversionszaehler":
        "application software version counter",
    "getriebe": "transmission", "obere": "upper", "oberer": "upper",
    "untere": "lower", "unterer": "lower", "mittlere": "middle",
    "targadach": "Targa roof", "sommer": "summer", "winter": "winter",
    "testerdatumkalibrierung": "tester date calibration",
    "testerkennungkodierung": "tester identification coding",
    "testerkennungkalibrierung": "tester identification calibration",
    "flashprogrammierung": "flash programming",
    "datensatzkennung": "data record identifier",
    "programmierungen": "programmings", "betaetigungen": "actuations",
    "heckleuchten": "rear lights", "heckleuchte": "rear light",
    "begrenzungslicht": "position light",
    "vorfeldbeleuchtung": "approach lighting",
    "ambientebeleuchtung": "ambient lighting",
    "tuerinnengriffbeleuchtung": "door inner handle lighting",
    "kindersicherung": "child lock",
    "verdecksteuerung": "convertible top control",
    "spiegelheizung": "mirror heating",
    "spiegelabsenkung": "mirror tilt-down",
    "positionsabspeicherung": "position storing",
    "spiegelanklappung": "mirror fold-in",
    "aussenspiegelheizung": "exterior mirror heating",
    "synchrone": "synchronous",
    "spiegelverstellung": "mirror adjustment",
    "leichtpanzer": "light armor", "schwerpanzer": "heavy armor",
    "sonderfahrzeug": "special vehicle",
    "crashentriegeln": "crash unlocking", "sitzmemory": "seat memory",
    "handschuhkastenverriegelung": "glovebox locking",
    "laendercodierung": "country coding",
    "fahrzeugparameter": "vehicle parameters",
    "umschaltung": "switchover", "sitzverstellung": "seat adjustment",
    "schloss": "lock", "bewegung": "movement",
    "geschwindigkeitssignal": "speed signal",
    "spiegelheizleistung": "mirror heating power",
    "produktionsmontage": "production assembly",
    "eingeschraenkter": "restricted", "eingeschraenkt": "restricted",
    "verstellmodus": "adjustment mode", "uebernahme": "takeover",
    "funkschluesselnummer": "radio key number",
    "funkschluesselmemorymodus": "radio key memory mode",
    "fahrberechtigung": "drive authorization",
    "akustische": "acoustic", "akustisch": "acoustic",
    "speicherung": "storing", "lenkerseite": "steering side",
    "tastenmemory": "button memory",
    "tuersteuergeraet": "door control unit",
    "kommunikationsanforderung": "communication request",
    "applikation": "application",
    "herstellerwerkskennzeichnung": "manufacturer plant code",
    "positionierung": "positioning",
    # third round: seat / lighting parameter tails
    "rueckmeldung": "feedback",
    "sitzbreitenverstellung": "seat width adjustment",
    "lehnenbreitenverstellung": "backrest width adjustment",
    "sitztiefenverstellung": "seat depth adjustment",
    "sitztiefeverstellung": "seat depth adjustment",
    "laengsverstellung": "longitudinal adjustment",
    "neigungsverstellung": "tilt adjustment",
    "lehnenneigungsverstellung": "backrest tilt adjustment",
    "sitzlaengsverstellung": "seat longitudinal adjustment",
    "sitzhoehenverstellung": "seat height adjustment",
    "sitzneigungsverstellung": "seat tilt adjustment",
    "kopfstuetzenverstellung": "headrest adjustment",
    "lehnenkopfverstellung": "backrest head adjustment",
    "gurthoehenverstellung": "belt height adjustment",
    "memoryverstellung": "memory adjustment",
    "handverstellung": "manual adjustment",
    "lordosenhoehe": "lumbar height",
    "lordosenweite": "lumbar width",
    "sitzweite": "seat width", "lehnenbreite": "backrest width",
    "lehnenneigung": "backrest tilt",
    "softblockverstellzaehler": "soft block adjustment counter",
    "hardwareverfahrweg": "hardware travel",
    "verfahrgeschwindigkeit": "travel speed",
    "wecken": "wake", "groesser": "greater than",
    "entlueftung": "venting",
    "wartezeitbegrenzung": "wait time limit",
    "wartezeit": "wait time", "entfall": "omission",
    "positionsfahrt": "position travel",
    "positionsspeicherung": "position storing",
    "fernlichtanhebung": "high beam raising",
    "referenzierung": "referencing",
    "laenderkodierung": "country coding",
    "veraenderung": "change",
    "geschwindigkeitsgrenze": "speed limit",
    "welcher": "which", "immer": "always",
    "dynamisch": "dynamically", "regelt": "controls",
    "zurueck": "back",
    # fourth round: aggressive compound handling
    "panoramadach": "panoramic roof",
    "verdeckcodierungen": "roof coding",
    "verdeckcodierung": "roof coding",
    "ablagefixierung": "shelf securing mechanism",
    "tippbetrieb": "one-touch operation",
    "komfortfunktion": "comfort function",
    "fernbedienung": "remote control",
    "klappe": "flap", "klappen": "flaps",
    "betrieb": "operation", "stellung": "position",
    "modul": "module", "nummer": "number",
    "werkseinstellung": "factory setting",
    "grossdach": "large roof", "hardtop": "hardtop",
    # fifth round: full-tail sweep (goal: no visible German at all)
    "baugruppe": "assembly", "netzwerkpowermgmt": "network power mgmt",
    "sollverbauliste": "target installation list",
    "istverbauliste": "actual installation list",
    "verbaut": "installed", "glasdach": "glass roof",
    "motortemperatur": "motor temperature",
    "kontrolleuchten": "indicator lamps",
    "digitaltacho": "digital speedometer", "hauptdisplay": "main display",
    "porschehardwareteilenummer": "Porsche hardware part number",
    "hardwareversionszaehler": "hardware version counter",
    "systemnameodermotortyp": "system name or motor type",
    "aktivediagnosesession": "active diagnostic session",
    "programmierbar": "programmable",
    "diagnostizierbar": "diagnosable",
    "diagnostizierbarkeit": "diagnosability",
    "baureihe": "model series", "produktionsmodus": "production mode",
    "produktionsnummer": "production number",
    "sensorcluster": "sensor cluster", "memorytaster": "memory button",
    "fehlerflag": "fault flag", "sperrklinke": "locking pawl",
    "systemfehler": "system fault", "treiber": "driver",
    "komponentenstatus": "component status",
    "rekuvorbedingungen": "recuperation preconditions",
    "ergebnisse": "results", "sporadische": "sporadic",
    "ausgabekennline": "output characteristic",
    "korrekturkennlinie": "correction characteristic",
    "freier": "free", "gross": "large",
    "spoilersteuerung": "spoiler control",
    "spoilerparameter": "spoiler parameters", "funk": "radio",
    "stecker": "connector", "warnton": "warning tone",
    "ventilhubverstellung": "valve lift adjustment",
    "batterieaberkennung": "battery rejection",
    "vetobedingungen": "veto conditions",
    "lenkwinkelsensor": "steering angle sensor",
    "dimmwert": "dimming value",
    "verschlusshaken": "locking hook", "fanghaken": "catch hook",
    "ansteuerzeit": "activation time", "kompass": "compass",
    "hydraulik": "hydraulics", "bautag": "build day",
    "druckreglerstrom": "pressure regulator current",
    "schluesselsuche": "key search", "lenkhilfe": "power steering",
    "schaerfephase": "arming phase", "motorlauf": "engine running",
    "detektionszeit": "detection time",
    "tastenbelegung": "button assignment",
    "aufschaltung": "activation",
    "kalibrierwerte": "calibration values",
    "lasterfassung": "load detection",
    "tastenentprellzeit": "button debounce time",
    "fahrerauswahl": "driver selection", "akustik": "acoustics",
    "codierdatum": "coding date", "blinken": "flashing",
    "lokaler": "local", "personen": "personal",
    "signalwert": "signal value", "gesendeter": "sent",
    "spitzenwert": "peak value", "anfahrstrom": "starting current",
    "zeitspanne": "time span", "steigung": "slope",
    "verringerung": "reduction", "routingtabelle": "routing table",
    "extern": "external", "abspeicherung": "storing",
    "reifendruckkontrolle": "tire pressure monitoring",
    "leistungselektronik": "power electronics",
    "busfehlermanager": "bus fault manager",
    "hauptwartung": "main service",
    "zwischenwartung": "interim service", "strecke": "distance",
    "nadelposition": "needle position", "ausfall": "failure",
    "tripdaten": "trip data", "passiv": "passive",
    "passivtaster": "passive button", "schiebedach": "sunroof",
    "spielschutz": "anti-play protection", "neues": "new",
    "datenobjekt": "data object", "kundendienst": "customer service",
    "plausibilisierung": "plausibility check",
    "schwellwert": "threshold value",
    "helligkeitsinfo": "brightness info",
    "fertigungsdatum": "production date", "mittlerer": "middle",
    "osten": "East", "satellitenskala": "satellite scale",
    "hauptskala": "main scale", "zeiger": "needles",
    "nachtankmethode": "refueling method",
    "niveausensor": "level sensor", "automatische": "automatic",
    "zielbremsung": "target braking",
    "nachlaufzeit": "afterrun time", "soll": "target",
    "sensorkodierung": "sensor coding",
    "messwertblock": "measured value block",
    "horizontale": "horizontal", "vertikale": "vertical",
    "durschnitt": "average", "durchschnitt": "average",
    "wegstreckespanne": "distance span",
    "plausi-zone": "plausibility zone", "hat": "has",
    "geantwortet": "responded", "ursache": "cause",
    "busse": "buses", "ausschalten": "switch off",
    "zielzeit": "target time",
    "zielleuchtdichten": "target luminances",
    "leuchtdichten": "luminances", "ausgabespannung": "output voltage",
    "mindesthelligkeit": "minimum brightness",
    "slavenummer": "slave number", "zulieferer": "supplier",
    "typ": "type", "verdeckmotor": "convertible top motor",
    "abschaltzeit": "switch-off time",
    "energiemanagement": "energy management",
    "bezogen": "referenced", "wann": "when", "ab": "from",
    "fuehrt": "leads", "steigende": "rising", "fallende": "falling",
    "hochdimmen": "dim-up", "auf": "at", "unbekannter": "unknown",
    "systemauswertung": "system evaluation",
    "innenraumueberwachung": "interior monitoring",
    "hochdruck": "high pressure", "niederdruck": "low pressure",
    # sixth round: remaining German words from the full-tail sweep
    "grad": "degrees", "optionen": "options",
    "sportmodus": "sport mode", "knotenadresse": "node address",
    "startstopp": "start-stop", "produktion": "production",
    "abstellzeit": "parking time", "abbiegelicht": "turning light",
    "hinterachslenkung": "rear axle steering",
    "ausgangsfilterkoeffizient": "output filter coefficient",
    "hochschaltpfeil": "upshift arrow", "menuepunkt": "menu item",
    "uebergaenge": "transitions", "harte": "hard",
    "codiert": "coded", "bis": "until",
    "orientierungslicht": "orientation light",
    "interner": "internal", "interne": "internal",
    "befuellphase": "fill phase", "entlueftphase": "vent phase",
    "startanforderung": "start request", "direkter": "direct",
    "hin": "out", "sperrungen": "locks",
    "fernlichtassistent": "high beam assistant",
    "aktivierungbits": "activation bits",
    "verfuegbarkeit": "availability",
    "restlaufanzeige": "remaining distance display",
    "pkw": "passenger car", "betaetigen": "actuate",
    "fahren": "driving", "aufbaubeschleunigung": "body acceleration",
    "ausgabe": "output", "passwort": "password",
    "leistungsreduzierung": "power reduction",
    "alarmeingang": "alarm input", "prio": "priority",
    "niedrig": "low", "poti": "potentiometer",
    "funktionsfreischaltung": "function enablement",
    "regen": "rain", "sprachbediensystem": "voice control system",
    "thermomanagement": "thermal management", "kamera": "camera",
    "ueberholverbot": "overtaking ban",
    "strommessung": "current measurement", "gesetzt": "set",
    "a-saeule": "A-pillar", "c-saeule": "C-pillar",
    "batterieabrissdauer": "battery disconnect duration",
    "eingriff": "intervention", "vorzeichen": "sign",
    "umschaltpunkt": "switchover point",
    "verfahrstrom": "travel current", "haltestrom": "holding current",
    "lichthupenposition": "headlight flash position",
    "failsafeposition": "failsafe position",
    "fahrdistanz": "driving distance", "sofortige": "immediate",
    "geradeausfahrt": "straight-ahead driving",
    "laendercode": "country code", "asien": "Asia",
    "pazifik": "Pacific", "produktionszeit": "production time",
    "walze": "roller", "befindet sich": "is",
    "generierung": "generation", "gelb": "yellow", "frei": "free",
    "waehlbar": "selectable", "bitte": "please",
    "temporaer": "temporarily", "ueberschritten": "exceeded",
    "belagwiderstand": "coating resistance",
    "uebertemperatur": "overtemperature", "empfangener": "received",
    "haengt": "stuck", "rueckfahrlicht": "reversing light",
    "entwicklungscodierungen": "development codings",
    "muss": "must", "ausgeloest": "triggered",
    "entsorgungszuendung": "disposal ignition",
    "entsorungszuendung": "disposal ignition",
    "heizen": "heating", "tonausgabedauer": "tone output duration",
    "tondauer": "tone duration", "touristenmodus": "tourist mode",
    "linksverkehr": "left-hand traffic",
    "rechtsverkehr": "right-hand traffic", "umgebung": "environment",
    "waschen": "washing", "programmierzustand": "programming state",
    "flaeche": "area", "schlusslicht": "tail light",
    "gesperrt": "locked", "nebelschlusslicht": "rear fog light",
    "verwendung": "usage",
    "batttempunterschreitung": "battery temp undershoot",
    "alarmzeit": "alarm time", "vorwarnung": "pre-warning",
    "anfang": "start", "wechselwirkung": "interaction",
    "korrekturfaktor": "correction factor", "seite": "side",
    "gasse": "gate", "korrekturwert": "correction value",
    "automatischer": "automatic", "einlassventil": "intake valve",
    "auslassventil": "exhaust valve", "lampen": "lamps",
    "gut": "good", "telegramme": "telegrams",
    "restlebensdauer": "remaining service life",
    "dauertonbereich": "continuous tone range",
    "tonparameter": "tone parameters",
    "schneelastfunktion": "snow load function",
    "ausfahrblockade": "extension blockage",
    "autoholdlampe": "auto-hold lamp", "warnruck": "warning jerk",
    "polaritaet": "polarity", "lager": "bearing",
    "linkes": "left", "rechtes": "right",
    "oelueberfuellungsanzeige": "oil overfill display",
    "geschwindigkerit": "speed",
    "aufhebungszeit": "cancellation time",
    "aufhebungszeichen": "cancellation sign",
    "dauerton": "continuous tone",
    "blockstrommessung": "blocking current measurement",
    "autobanhnlicht": "highway light", "eingebaut": "installed",
    "erreicht": "reached",
    # seventh round: last frequency tier of visible German words
    "positioniersystem": "positioning system",
    "positioniersysteme": "positioning systems",
    "seitensatellit": "side satellite",
    "wiederverriegelung": "re-locking",
    "rueckschwenken": "swivel-back",
    "vorderachssensor": "front axle sensor",
    "hinterachssensor": "rear axle sensor",
    "lehnenneigungsverst": "backrest tilt adj",
    "sitzneigungsverst": "seat tilt adj",
    "sitzlaengsverst": "seat longitudinal adj",
    "sitzhoehenverst": "seat height adj",
    "sw-endanschlaege": "SW end stops",
    "lernstatus": "learning status",
    "grenzgeschwindigkeit": "limit speed", "eingelegt": "engaged",
    "basisschloss": "basic lock", "codierter": "coded",
    "rastenansteuerung": "detent activation",
    "heckscheibe": "rear window", "heizbare": "heatable",
    "nebelschlussleuchte": "rear fog lamp", "zweite": "second",
    "dritte": "third", "anfahren": "moving to",
    "ausstelllage": "tilt position", "ausstellen": "tilt out",
    "schiebebereich": "slide range", "automatik": "automatic",
    "ambientelicht": "ambient light",
    "korrekturfunktion": "correction function",
    "daemmglas": "insulating glass", "ruecklaufstop": "return stop",
    "werden": "be", "lehnenkopf": "backrest head",
    "widerstandskodiert": "resistance-coded",
    "stromkodiert": "current-coded",
    "ausblasfuehler": "outlet air sensor",
    "seitenduese": "side vent", "fussraum": "footwell",
    "kuehlen": "cooling", "entfernen": "remove", "nah": "near",
    "fern": "far", "schwergaengig": "stiff",
    "touristemmodusposition": "tourist mode position",
    "lichthupe": "headlight flash", "uebernommen": "adopted",
    "statische": "static", "waehrend": "during",
    "ausblendzeit": "fade-out time",
    "kurvensummandenstuetzstelle":
        "curve summand interpolation point",
    "zeitfenster": "time window",
    "klingellautstaerke": "ring volume",
    "radimpulszaehler": "wheel pulse counter",
    "impulszaehler": "pulse counter", "vornummer": "pre-number",
    "aktuatorversorgung": "actuator supply",
    "basisgroessen": "basic quantities",
    "tempomat": "cruise control", "magneten": "magnet",
    "unter": "below",
    # eighth round: complete sweep of remaining German compounds
    "bandende": "end-of-line",
    "bandendekonfiguration": "end-of-line configuration",
    "kabelbaum": "wiring harness",
    "kabelbaumueberwachung": "wiring harness monitoring",
    "kabelbaumwiderstand": "wiring harness resistance",
    "logischer": "logical", "kalenderwoche": "calendar week",
    "steilheitsfaktor": "slope factor", "anzuzeigende": "displayed",
    "programmiervorbedingungen": "programming preconditions",
    "rueckrufaktion": "recall action",
    "einzeloeffnung": "single opening",
    "nachwischzyklen": "after-wipe cycles",
    "mindestanzeigezeit": "minimum display time",
    "statistische": "statistical",
    "geschwindigkeitsbeschraenkung": "speed restriction",
    "ueberhitzt": "overheated", "dauerhaft": "permanently",
    "druckabweichung": "pressure deviation",
    "sammelmeldung": "collective message",
    "servicestellung": "service position",
    "sicherheitslampe": "safety lamp", "menuebaum": "menu tree",
    "menuelaenge": "menu length", "menueabschnits": "menu section",
    "menuezeile": "menu line", "menuepfad": "menu path",
    "menue": "menu", "ungedaempfte": "undamped",
    "ungedaempft": "undamped", "gedaempft": "damped",
    "gedaempfte": "damped", "gedaempftes": "damped",
    "drehzahlfuehler": "speed sensor", "fuehler": "sensor",
    "luftspalt": "air gap", "luftspaltmonitoring": "air gap monitoring",
    "aktualitaet": "validity", "zeitangabe": "time indication",
    "systeminformation": "system information",
    "heck-wisch-waschprogramm": "rear wipe/wash program",
    "leselicht": "reading light", "abhaengige": "dependent",
    "abhaengig": "dependent", "abhaengiges": "dependent",
    "unabhaengige": "independent", "umschaltzeit": "switchover time",
    "kuehlmittelausgleichsbehaelter": "coolant expansion tank",
    "behaelter": "tank", "ruecklauf": "return", "laufende": "running",
    "luftverteilung": "air distribution",
    "belueftungsfeld": "ventilation field", "haltezeit": "hold time",
    "aenderung": "change", "aenderungen": "changes",
    "aenderungsdatum": "change date",
    "aenderungsfaktor": "change factor",
    "navigationssoftware": "navigation software",
    "sprachbedienung": "voice control",
    "handyvorbereitung": "phone preparation", "hoerer": "handset",
    "konvertierung": "conversion",
    "tankentlueftungssystem": "tank venting system",
    "tankentlueftungsventil": "tank venting valve",
    "verbleibende": "remaining", "verbleibender": "remaining",
    "verzoegerungszeitein": "switch-on delay time",
    "aufdimmgeschwindigkeit": "dim-up speed",
    "abdimmgeschwindigkeit": "dim-down speed",
    "batteriestatusberechnung": "battery status calculation",
    "berechnung": "calculation", "nachregelung": "readjustment",
    "klemmbedinung": "terminal operation",
    "startfaehigkeit": "starting capability",
    "batteriesaeure": "battery acid", "parklicht": "parking light",
    "versionzaehler": "version counter", "zifferblaetter": "dials",
    "reifengroesse": "tire size", "unkorrigiertes": "uncorrected",
    "unkorrigiert": "uncorrected", "korrigiert": "corrected",
    "ruecksetzdatum": "reset date", "ruecksetzen": "reset",
    "ruecksetzbedingung": "reset condition",
    "programmstand": "program version",
    "druckmessung": "pressure measurement",
    "hydraulischer": "hydraulic", "hydraulisches": "hydraulic",
    "bremsassistent": "brake assist", "vorbefuellung": "pre-fill",
    "vergleiche": "comparisons", "ausfuehrung": "execution",
    "rasten": "detent", "vorrasten": "pre-detent",
    "hauptrasten": "main detent",
    "rueckfahrscheinwerfer": "reversing headlight",
    "wertes": "value", "optische": "optical",
    "alarmausgabe": "alarm output",
    "zwangskopplung": "forced coupling",
    "sequenzieller": "sequential", "ablauf": "sequence",
    "speedabhaengige": "speed-dependent",
    "schalterbedienung": "switch operation",
    "fortfuehrung": "continuation", "blockierzeit": "blocking time",
    "pausenzeit": "pause time", "entnormiert": "denormalized",
    "timeoutwert": "timeout value", "rotordrehzahl": "rotor speed",
    "sortenschluessel": "variant key",
    "herstellerwerkskennzahl": "manufacturer plant code number",
    "schiebetuere": "sliding door", "reduzierte": "reduced",
    "blockzeit": "blocking time",
    "tuerinnengrifftaster": "door inner handle button",
    "tueraussengrifftaster": "door outer handle button",
    "neigungssensortaster": "tilt sensor button",
    "gegenueber": "opposite", "ungefilterte": "unfiltered",
    "funkchluesselverifikation": "radio key verification",
    "aktivitaet": "activity", "zulaesst": "permits",
    "laengsverst": "longitudinal adj", "laengverst": "longitudinal adj",
    "laengsversteller": "longitudinal adjuster",
    "neigungsverst": "tilt adj",
    "systembasischip": "system basis chip",
    "ueberlastet": "overloaded",
    "batterietrennung": "battery disconnection",
    "reserviert": "reserved", "fotosensorwert": "photosensor value",
    "steuerplatine": "control board",
    "laendervariante": "country variant", "stroeme": "currents",
    "sitzbelueftung": "seat ventilation",
    "entwicklungsbotschaften": "development messages",
    "anzeigebereitschaft": "display readiness",
    "abschaltgeschwindigkeit": "switch-off speed",
    "einschaltgeschwindigkeit": "switch-on speed",
    "annaehern": "approach", "annaeherung": "approach",
    "tonabschaltung": "tone switch-off", "beginn": "beginning",
    "aktivierungsberechnung": "activation calculation",
    "normalleistung": "normal power",
    "anbauhoehe": "mounting height",
    "wischerparkposition": "wiper park position",
    "achsensorfilterung": "axle sensor filtering",
    "gewichtungsfaktor": "weighting factor",
    "kurvenkruemmung": "curve curvature",
    "strassenaeusserer": "road-outer", "brenndauer": "burn time",
    "xenonbrenner": "xenon burner", "elektronische": "electronic",
    "klimaanlage": "air conditioning",
    "nachleuchtdauer": "afterglow duration",
    "integriertes": "integrated",
    "bluetoothmodul": "Bluetooth module",
    "entwicklung": "development",
    "sensorversorgung": "sensor supply", "versorgung": "supply",
    "rennstartzaehler": "race start counter",
    "kurzzeitmemory": "short-time memory",
    "trimmregelung": "trim control",
    "lambdasondenvertauschung": "oxygen sensor swap",
    "sekundaerluftsystem": "secondary air system",
    "kraftstoffversorgung": "fuel supply",
    "kommunikation": "communication", "lebensdauer": "service life",
    "anzulernender": "to-be-learned", "seitlich": "lateral",
    "einschaltdauer": "switch-on duration", "sprung": "jump",
    "folgenden": "following",
    "richtungsumkehr": "direction reversal",
    "drucktank": "pressure tank", "tankklappe": "fuel filler flap",
    "waschvorgaenge": "wash operations", "erstmaligen": "first",
    "gleitende": "sliding", "leuchtweite": "beam range",
    "eigenschaften": "properties", "blockzaehler": "block counter",
    "erweiterung": "extension", "alterung": "aging",
    "kodiert": "coded", "sichtbar": "visible",
    "koeffizient": "coefficient",
    "displayhelligkeitjustierung": "display brightness adjustment",
    "dimmungsstellknopf": "dimming knob", "stellknopf": "knob",
    "expertenmenue": "expert menu",
    "anzeigeberuhigung": "display smoothing",
    "wartungsintervall": "service interval",
    "zeitliches": "temporal", "zeitliche": "temporal",
    "oberes": "upper", "eintrittsschwelle": "entry threshold",
    "austrittsschwelle": "exit threshold",
    "tankberuhigungszeit": "tank settling time",
    "naesse": "wet conditions", "uebernehmen": "take over",
    "bestaetigen": "confirm", "ungueltiger": "invalid",
    "anfahrbefehl": "drive-off command", "stehendes": "stationary",
    "voraus": "ahead", "objekt": "object",
    "kuehlmitelstand": "coolant level",
    "kennzeichenbeleuchtung": "license plate light",
    "tagfagrlicht": "daytime running light",
    "bremsfluessigkeit": "brake fluid",
    "bremsfluessigkeitsstand": "brake fluid level",
    "parklichtwarnung": "parking light warning",
    "zuerst": "first", "drehen": "turn",
    "zuendungseinschaltversuch": "ignition switch-on attempt",
    "zweiter": "second", "loesen": "release",
    "verbraucherabschaltung": "consumer shutdown",
    "quiettierton": "acknowledge tone",
    "motorspuelgeblaese": "engine purge blower",
    "bremskraftverteilung": "brake force distribution",
    "solldruecke": "target pressures",
    "istdruecke": "actual pressures", "rundstrecke": "race track",
    "neue": "new", "navigationsdaten": "navigation data",
    "lifsteuerung": "lift control", "zusaetzliches": "additional",
    "anzeigesegment": "display segment", "qualitaet": "quality",
    "voreilung": "lead", "drehung": "rotation",
    "erhoehen": "increase", "kanaele": "channels",
    "verbundstand": "network level",
    "fuellzeichen": "fill character",
    "sensororientierung": "sensor orientation",
    "fahrsituation": "driving situation",
    "lenkmomentempfehlung": "steering torque recommendation",
    "rueckfoerderpumpe": "return pump",
    "initialisiert": "initialized", "initalisiert": "initialized",
    "abweichung": "deviation",
    "einschleifweg": "grinding-in distance",
    "mindestdruck": "minimum pressure",
    "hf-ueberdeckung": "RF coverage", "faehrt": "driving",
    "pinzustaende": "pin states",
    "karrosseriestecker": "body connector",
    "dbreduzierung": "dB reduction",
    "blockstromerkennung": "blocking current detection",
    "ansteuerpuls": "activation pulse",
    "frontwischzyklen": "front wipe cycles",
    "mindestbetaetigungszeit": "minimum actuation time",
    "lichtpack": "light package",
    "stahlgrossdach": "steel large roof",
    "lichtkomfortpaket": "light comfort package",
    "tuertafel": "door panel", "rampensteilheit": "ramp slope",
    "verzoegerte": "delayed",
    "synchronisation": "synchronization",
    "synchronisationsverlust": "synchronization loss",
    "kuehlwaserstandsschwelle": "cooling water level threshold",
    "ausfahrblockierung": "extension blocking",
    "einfahrblockierung": "retraction blocking",
    "lageueberwachung": "position monitoring",
    "bugspoiler": "front spoiler", "reversieren": "reversing",
    "dekrementierungszeit": "decrement time",
    "verfahrrichtung": "travel direction",
    "abdunkelungsgrad": "darkening level",
    "fahrphysikalischer": "driving physics",
    "momentenreduktion": "torque reduction",
    "querverbau": "cross installation",
    "abschalltung": "switch-off", "endstufe": "output stage",
    "anlernbedingung": "teach-in condition", "verletzt": "violated",
    "heligkeitsinfo": "brightness info",
    "aufmerksamkeitsherhoehung": "attention increase",
    "deaktiverungslampe": "deactivation lamp",
    "kodierungskompatibilitaet": "coding compatibility",
    "kindersitz": "child seat", "falsch": "incorrectly",
    "positioniert": "positioned",
    "stoersignal": "interference signal",
    "kindersitzerkennung": "child seat detection",
    "qualifizierter": "qualified", "gueltigkeit": "validity",
    "ersatzteilsteuergeraet": "spare part control unit",
    "fuehlerposition": "sensor position",
    "lenkstockspalt": "steering column gap",
    "gezielt": "specifically", "senden": "send",
    "vereisungsschutz": "icing protection",
    "unterdruckabschaltung": "vacuum switch-off",
    "unterdrucksystem": "vacuum system", "restzeit": "remaining time",
    "kompressoreinlaufphase": "compressor run-in phase",
    "benoetigte": "required", "bloecke": "blocks",
    "beendet": "finished",
    "fussraumausstroemer": "footwell vent",
    "personenausstroemer": "person vent",
    "defrostausstroemer": "defrost vent",
    "maximalzeitueberschreitung": "maximum time exceedance",
    "motormindestlaufzeit": "engine minimum run time",
    "stopsperre": "stop inhibit",
    "ursprungsbedatung": "original data set",
    "handschaltung": "manual shift",
    "erstwarntondauer": "first warning tone duration",
    "einschaltzueklen": "switch-on cycles",
    "anfangsgespraechslautstaerke": "initial call volume",
    "lautstaerkebegrenzung": "volume limit",
    "lautstaerke": "volume", "einstellbarkeit": "adjustability",
    "kaelter": "colder", "kartendarstellung": "map display",
    "fahrtenbuchmenueanzeigezeit": "logbook menu display time",
    "fahrtenbuchmenue": "logbook menu",
    "unprogrammiert": "unprogrammed",
    "geraeteadresse": "device address",
    "softwarestandsbezeichnung": "software version designation",
    "musterstand": "sample level", "schaltgeraet": "shift device",
    "anforderung": "request", "tastung": "keying",
    "bremsinformation": "brake information",
    "fahrgeschwindigkeitsregler": "cruise control",
    "wunschdrehzahl": "desired speed",
    "drehzahllimitierung": "speed limiting",
    "drehzahlregler": "speed controller",
    "drehzahllimit": "speed limit",
    "kuehlwasserabsperrventil": "cooling water shutoff valve",
    "plausibilisierte": "plausibility-checked",
    "abtriebsdrehzahl": "output speed",
    "funktionalitaet": "functionality",
    "geschwindigkeitsregelanlage": "cruise control system",
    "reinigungsanlage": "cleaning system", "belegung": "assignment",
    "jokertaste": "joker button", "haengender": "stuck",
    "lenkstockschalter": "steering column switch",
    "tipzeit": "tip time", "beschleunigen": "accelerate",
    "verzoegern": "decelerate", "defaultwert": "default value",
    "helligkeitsreglung": "brightness control",
    "uebergang": "transition",
    "geraeuschtypisierung": "noise typing",
    "navigationssystem": "navigation system",
    "wartungsdienste": "maintenance services",
    "typisierungsnummer": "typing number",
    "garagentoroeffner": "garage door opener",
    "lebenszustand": "life state",
    "identitaetszaehler": "identity counter",
    "markenkennung": "brand identifier",
    "schluesselanlernen": "key teach-in",
    "authentifizierbar": "authenticatable",
    "verriegelungsmodes": "locking modes",
    "entriegelungsmode": "unlocking mode",
    "doppelbetaetigungszeit": "double actuation time",
    "bedieneinheiten": "operating units",
    "unterscheidung": "distinction", "lang": "long", "kurz": "short",
    "ausschaltschaltzeit": "switch-off time",
    "veriegelungsphase": "locking phase",
    "halogenabblendlicht": "halogen low beam",
    "mindesteinschaltdauer": "minimum on duration",
    "hebelarmlaenge": "lever arm length",
    "einschaltverzoegerung": "switch-on delay",
    "ausstelldach": "pop-up roof", "schiebe": "sliding",
    "autobahnfahrlicht": "highway driving light",
    "seitenmarkierungsleuchten": "side marker lights",
    "umschaltblende": "switchover bezel", "blende": "bezel",
    "zusatzfernlicht": "auxiliary high beam",
    "scheinwerferdiebstahlueberwachung": "headlight theft monitoring",
    "oeffnet": "opens", "akustischer": "acoustic",
    "verriegeln": "locking", "beruecksichtigen": "consider",
    "blockierter": "blocked",
    "entriegelungsversuch": "unlocking attempt",
    "verriegelungsanforderung": "locking request",
    "nachfuehrung": "tracking", "querversatz": "lateral offset",
    "tastenentprellpruefungen": "button debounce checks",
    "sendezeit": "transmit time", "timeoutzeit": "timeout time",
    "messungen": "measurements",
    "wischstufenrueckschaltung": "wiper stage downshift",
    "traenenfunktion": "tear-wipe function",
    "tip-wischen": "tip wiping", "auswerten": "evaluate",
    "wischeranforderung": "wiper request", "sofort": "immediately",
    "wirksam": "effective", "wendelage": "reversal position",
    "tastender": "momentary",
    "zwischenstellung": "intermediate position",
    "bedienung": "operation", "sendeeinheit": "transmit unit",
    "empfangsfrequenz": "receive frequency",
    "nullstellung": "zero position", "dunkelheit": "darkness",
    "aktuell": "current", "auslastung": "utilization",
    "generatorauslastung": "alternator utilization",
    "batterieregeneration": "battery regeneration",
    "standheizungstimer": "auxiliary heating timer",
    "generatorgroesse": "alternator size",
    "reglergeneration": "regulator generation",
    "geschwindigkeitslimit": "speed limit",
    "luftstroemung": "air flow",
    "schaltempfehlung": "shift recommendation",
    "wunschgeschwindigkeit": "desired speed",
    "skalenbeleuchtung": "scale illumination",
    "autorepeatgeschwindigkeit": "autorepeat speed",
    "ganginformation": "gear information",
    "darstellung": "representation", "uebertragen": "transferred",
    "restliche": "remaining", "zeichen": "signs",
    "animierter": "animated", "startbildschirm": "start screen",
    "berechnungslogik": "calculation logic",
    "anzeigbar": "displayable", "skalierung": "scaling",
    "erhoehtes": "increased",
    "uhrzeitsynchronisation": "time synchronization",
    "theoretisches": "theoretical",
    "nachtankvolumen": "refuel volume",
    "plausibilitaetsvolumen": "plausibility volume",
    "plausibiltaetsvolumen": "plausibility volume",
    "displaykontrollerspannung": "display controller voltage",
    "tankfuellstand": "tank fill level", "selektierte": "selected",
    "tankkorrekturwert": "tank correction value",
    "dimmtastenwert": "dim button value",
    "rueckstelltopf": "reset counter", "litermenge": "liter quantity",
    "menge": "quantity",
    "antwortendes": "responding", "entnehmbare": "extractable",
    "sendemode": "transmit mode", "bedienhoerer": "handset",
    "hexwert": "hex value", "indikation": "indication",
    "bitwerte": "bit values", "hauptsicherungsbox": "main fuse box",
    "verbrauchsguenstiger": "fuel-efficient",
    "waehlbarer": "selectable", "in ordnung": "OK",
    "saeureschichtung": "acid stratification",
    "haendlernummer": "dealer number", "bargraf": "bar graph",
    # ninth round: prose words (function words, adjectives, verbs)
    # found by the prose-position sweep
    "wie": "how", "schnell": "quickly", "langsam": "slowly",
    "herunterdimmt": "dims down", "runterdimmt": "dims down",
    "heraufdimmt": "dims up", "hochdimmt": "dims up",
    "dimmt": "dims", "abdimmen": "dim-down", "dimmen": "dimming",
    "automatish": "automatically", "null": "zero",
    "zu hoher": "too high", "zu hohe": "too high",
    "zu hoch": "too high", "zu niedriger": "too low",
    "zu niedrige": "too low", "zu niedrig": "too low",
    "an sind": "are on", "haelt an": "is stopping",
    "hoher": "high", "hell": "bright", "dunkel": "dark",
    "heiss": "hot", "kalt": "cold", "heller": "brighter",
    "dunkler": "darker", "weiss": "white", "weisse": "white",
    "roter": "red", "rote": "red", "gelbe": "yellow",
    "weich": "soft", "weiche": "soft", "hart": "hard",
    "klein": "small", "kleine": "small", "kurze": "short",
    "noch": "still", "sich": "itself", "eines": "of a",
    "jedem": "every", "aller": "all", "mehrere": "multiple",
    "mehrfache": "multiple", "sein": "be", "gilt": "is considered",
    "stabil": "stable", "momentan": "currently",
    "sonstige": "other", "feste": "fixed", "aktiver": "active",
    "aktivem": "active", "relativer": "relative",
    "absolutes": "absolute", "absolut": "absolute",
    "konsekutiv": "consecutive", "seriell": "serial",
    "resistiv": "resistive", "asynchron": "asynchronous",
    "plausibel": "plausible", "indirekter": "indirect",
    "globales": "global", "selektives": "selective",
    "generelle": "general", "zentrales": "central",
    "letztem": "last", "letzt": "last", "lokal": "local",
    "intern": "internal", "vom": "from the",
    # verbs / participles
    "gelernt": "learned", "angelernter": "learned",
    "angefragt": "queried", "deaktivieren": "deactivate",
    "setzen": "set", "heben": "lift", "senken": "lower",
    "hebt": "raising", "senkt": "lowering", "abgesenkt": "lowered",
    "eingefahren": "retracted", "ausgefahren": "extended",
    "ausfahren": "extend", "einfahren": "retract",
    "aufgehoben": "cancelled", "reduzieren": "reduce",
    "uebernehmen": "take over", "bestaetigen": "confirm",
    "reinigen": "clean", "abschliessen": "lock",
    "entlasten": "relieve", "benutzen": "use",
    "nachladen": "recharge", "sichern": "secure",
    "bringen": "put", "starten": "start", "startet": "starts",
    "lernt": "learning", "aufsuchen": "visit", "ablegen": "stow",
    "aufgestellt": "raised", "abgelegt": "stowed",
    "angehoben": "raised", "angeklappt": "folded in",
    "blinkt": "flashing", "gesteckt": "latched",
    "steckt": "inserted", "anhalten": "stop",
    "zulassen": "permit", "zulaesst": "permits",
    "verloren": "lost", "gelockt": "locked",
    "auslesen": "read out", "unterschritten": "below minimum",
    "gefiltert": "filtered", "begrenzt": "limited",
    "begrenztes": "limited", "brauchen": "need", "lief": "ran",
    "bekannt": "known", "runter": "down", "gefahrene": "driven",
    "geradeaus": "straight ahead", "schwenken": "swivel",
    "positioniert": "positioned",
    # remaining nouns / compounds from the prose sweep
    "funktionsbereit": "operational",
    "tankleckdiagnose": "tank leak diagnosis",
    "feinstleck": "very fine leak", "dcdcwandler": "DC/DC converter",
    "energiedurchsatz": "energy throughput",
    "gesamtenergiedurchsatz": "total energy throughput",
    "segmentnummer": "segment number", "vorhalt": "lead",
    "windschott": "wind deflector", "modi": "modes",
    "verfahrdistanz": "travel distance",
    "referenezlauf": "reference run", "kammer": "chamber",
    "batterietechnologie": "battery technology",
    "technologie": "technology",
    "batteriemanagementsg": "battery management ECU",
    "soundparameter": "sound parameters", "laut": "loud",
    "leise": "quiet", "scharf": "armed", "eingabe": "input",
    "targaklappe": "Targa flap", "argentinien": "Argentina",
    "selbsttest": "self-test", "ausbeute": "yield",
    "thermoschutz": "thermal protection",
    "lordosenblase": "lumbar air bladder", "selbstlauf": "self-run",
    "seiten": "side", "schrittverlustkorrektur":
        "step loss correction", "schrittverluste": "step losses",
    "nachrichtengruppen": "message groups",
    "funktionenkatalog": "function catalog", "crashart": "crash type",
    "ende": "end", "farbe": "color", "nachtanken": "refueling",
    "nebelscheinwerfer": "front fog light",
    "seitenmarker": "side marker", "komplement": "complement",
    "karosserie": "body", "karosserietyp": "body type",
    "notbremsfunktion": "emergency brake function",
    "softwaredatum": "software date",
    "ausstiegsleuchte": "exit lamp", "empfang": "reception",
    "spoilerinfo": "spoiler info",
    "drehstabmoment": "torsion bar torque",
    "anlernvorgang": "teach-in process", "anlernen": "teach-in",
    "deaktiv": "deactivated", "weit": "far",
    "achssensor": "axle sensor", "fahrtenbuch": "logbook",
    "anschluss": "connection", "transportprotokoll":
        "transport protocol", "kunde": "customer",
    "magnetventil": "solenoid valve",
    "ausschaltschwelle": "switch-off threshold",
    "programm": "program", "werkstatt": "workshop",
    "tankdeckel": "fuel cap", "wechsler": "changer",
    "programmierdatum": "programming date", "ja": "yes",
    "fahrten": "trips", "datensatzname": "data record name",
    "kontainername": "container name", "land": "country",
    "umschaltventil": "switchover valve",
    "einschleifen": "grinding-in",
    "einschleifstatus": "grinding-in status",
    "einschleifmodus": "grinding-in mode",
    "luftspiel": "air clearance", "windabweiser": "wind deflector",
    "kennzeichenleuchte": "license plate lamp",
    "absenken": "lower", "handschuhfach": "glovebox",
    "offenem": "open", "schiebelage": "slide position",
    "zug": "pull", "wegfall": "omission",
    "gleichstromfrei": "DC-free", "verschluss": "latch",
    "verschlussmotor": "latch motor", "adresse": "address",
    "verhalten": "behavior", "vorfeldleuchte": "approach lamp",
    "kurzhub": "short stroke",
    "vorabkurzhub": "preliminary short stroke",
    "einklemmschutz": "anti-trap protection",
    "klappfunktion": "fold function", "vernetzter": "networked",
    "heiztrigger": "heating trigger",
    "sportwagenfunktion": "sports car function",
    "mehrfachblock": "multiple block", "locktaster": "lock button",
    "unlocktaster": "unlock button",
    "tankdeckeltaster": "fuel cap button",
    "eindrahtfehler": "single-wire fault", "notaus": "emergency stop",
    "spiegelauswahl": "mirror selection", "rippel": "ripple",
    "blockstrom": "blocking current", "drehfalle": "rotary latch",
    "drehfallenschloss": "rotary latch lock",
    "zuziehen": "soft-close", "zuziehilfe": "soft-close aid",
    "stromaufnahme": "current draw", "hallsensor": "Hall sensor",
    "sportschalensitz": "sport bucket seat",
    "memorykonzept": "memory concept", "softblock": "soft block",
    "blase": "air bladder", "hardwarefahrweg": "hardware travel",
    "hardwareverfahweg": "hardware travel",
    "throaxairbag": "thorax airbag", "frontplatine": "front board",
    "lenkervariante": "steering variant", "luftfeder": "air spring",
    "stauluftklappe": "ram air flap", "defrostklappe": "defrost flap",
    "innenraum": "interior", "wegimpulse": "distance pulses",
    "fehleraktion": "fault action", "stillsatnd": "standstill",
    "inkativ": "inactive", "startmodus": "start mode",
    "strasseninnerer": "road-inner", "stati": "states",
    "masterbestellnummer": "master order number",
    "verbund": "network", "eingangswelle": "input shaft",
    "fehlerpfade": "fault paths",
    "programmierversuche": "programming attempts",
    "dynamik": "dynamics", "ventilhub": "valve lift",
    "ventilhubsystem": "valve lift system",
    "alarmphase": "alarm phase", "crashblinken": "crash flashing",
    "tippblinken": "tip flashing",
    "abschalttemperatur": "switch-off temperature",
    "fenster": "window", "fenstern": "windows",
    "fondscheibe": "rear side window",
    "fondscheiben": "rear side windows", "abparken": "parking",
    "kryptologie": "cryptology", "tabelle": "table",
    "steckplatz": "slot", "auswahl": "selection",
    "nachziehen": "trailing", "farbumschlag": "color change",
    "durchschnittspuffers": "averaging buffer",
    "nachtankschwelle": "refuel threshold", "schritten": "steps",
    "leeres": "empty", "zusatzzeichen": "supplementary sign",
    "inhalte": "contents", "inhalt": "content",
    "rechtsabbieger": "right turns", "linksabbieger": "left turns",
    "bremsanlage": "brake system", "tankreserve": "fuel reserve",
    "sensorsicht": "sensor view", "bremsbelag": "brake pad",
    "bremsbelagverschleiss": "brake pad wear", "fzg": "vehicle",
    "komplettausfall": "complete failure", "wandler": "converter",
    "warmlauf": "warm-up", "servicehinweis": "service note",
    "rennstart": "race start", "rollenmodus": "dyno mode",
    "positionslose": "positionless",
    "einsatzgebiet": "coverage area", "mechanik": "mechanics",
    "diebstahlversuch": "theft attempt", "notruf": "emergency call",
    "codeeingabe": "code entry", "negativ": "negative",
    "materialindex": "material index",
    "parametersatzindex": "parameter set index",
    "parametersatzversion": "parameter set version",
    "kalibriervorgang": "calibration process", "modell": "model",
    "hinterrachse": "rear axle", "anfahrwunsch": "drive-off request",
    "weckgrund": "wake reason", "realer": "real",
    "routinen": "routines", "heckspoiler": "rear spoiler",
    "verdeckverschluss": "convertible top latch",
    "schlussleuchten": "tail lamps", "alarmsirene": "alarm siren",
    "bruchsensor": "breakage sensor",
    "alarmkontakten": "alarm contacts", "alarmzyklen": "alarm cycles",
    "alarmpause": "alarm pause", "alarme": "alarms",
    "motorraumtemperatur": "engine bay temperature",
    "einfahrschwelle": "retraction threshold",
    "hardwarediagnose": "hardware diagnosis",
    "aufstell": "tilt-out", "tastbetrieb": "momentary operation",
    "aufnahme": "receptacle", "kodierswitch": "coding switch",
    "antriebsart": "drive type",
    "hauptcontroller": "main controller",
    "safingcontroller": "safing controller",
    "sitzplatz": "seat place", "unbestimmter": "undetermined",
    "glasvariante": "glass variant",
    "codierreferenz": "coding reference", "konzept": "concept",
    "ansaugtemperatur": "intake temperature",
    "spannngsfehler": "voltage fault",
    "aussententemperatur": "outside temperature", "hub": "lift",
    "hyterese": "hysteresis", "erstwarnton": "first warning tone",
    "brenner": "burner", "farbcode": "color code",
    "ladeschalen": "charging cradles",
    "ladeschale": "charging cradle",
    "multifunktionslenkrad": "multifunction steering wheel",
    "multifunktionstaste": "multifunction button",
    "klimaprofil": "climate profile", "almanache": "almanacs",
    "videosignal": "video signal", "softwaretyp": "software type",
    "sachnummer": "item number", "fremdnummer": "external number",
    "textform": "text form", "kickdownschalter": "kickdown switch",
    "getriebesumpftemperatur": "transmission sump temperature",
    "istgang": "actual gear", "tiptronik": "Tiptronic",
    "signalhorn": "horn", "diskret": "discrete",
    "minuterie": "courtesy timer", "hundertstel": "hundredths",
    "bestelltyp": "order type", "kraftstoffmarkt": "fuel market",
    "verbauvorschrift": "installation rule",
    "verdeckfarbe": "convertible top color", "wagen": "car",
    "einlassnockenwelle": "intake camshaft",
    "startzyklen": "start cycles", "hellgrenze": "bright threshold",
    "dunkelgrenze": "dark threshold",
    "tippblinkzyklen": "tip flash cycles",
    "blinkpause": "flash pause", "gegenseite": "opposite side",
    "fehlalarme": "false alarms", "mitblinkend": "co-flashing",
    "vorschrift": "regulation", "motortstart": "engine start",
    "selekt": "select", "anfahrschlag": "start impact",
    "telegramm": "telegram", "autoparken": "auto-parking",
    "ausspuren": "disengage",
    "startwiederholsperre": "start repeat inhibit",
    "puffer": "buffer", "rauschen": "noise",
    "kompletter": "complete",
    "programmierfehler": "programming error",
    "programmierschritt": "programming step",
    "handbremsschalter": "handbrake switch",
    "datenpuffer": "data buffer", "feuchte": "humidity",
    "scannercode": "scanner code",
    "motorwiederstart": "engine restart", "wiedergabe": "playback",
    "fahrzeugauftrag": "vehicle order", "segel": "coasting",
    "diskrepanz": "discrepancy",
    "generatorhersteller": "alternator manufacturer",
    "spiegelabsenken": "mirror tilt-down", "hupe": "horn",
    "dienste": "services", "golfstaaten": "Gulf states",
    "tankauswahl": "tank selection", "tempostat": "cruise control",
    "tempostatanzeige": "cruise control display",
    "zifferblatt": "dial", "zifferblaetter": "dials",
    "trennlinie": "separator line",
    "balkendiagramm": "bar chart", "schrittweite": "step width",
    "verschleissindex": "wear index", "lasche": "tab",
    "tempolimit": "speed limit", "abbruch": "abort",
    "fototransistor": "phototransistor", "grafik": "graphics",
    "wahlhebel": "selector lever", "tankmodul": "tank module",
    "konstante": "constant",
    "leiterplattetemperatur": "circuit board temperature",
    "netzwerk": "network",
    "momentanverbrauch": "instantaneous consumption",
    "ohmscher": "ohmic", "zonen": "zones",
    # tenth round: Macan (95B) log - VW/MLB platform vocabulary
    "anwendung": "application", "sicher": "reliably",
    "zelle": "cell", "zell": "cell", "ringspeicher": "ring buffer",
    "verhinderter": "prevented", "verhindert": "prevented",
    "stoppvorgaenge": "stop operations",
    "stoppvorgang": "stop operation",
    "startvorgaenge": "start operations",
    "startvorgang": "start operation",
    "fehlerbitmaske": "fault bit mask", "kodierdaten": "coding data",
    "angeforderter": "requested", "angeforderte": "requested",
    "batteriebilanzierung": "battery balancing",
    "energiebilanzierung": "energy balancing",
    "bilanzierung": "balancing", "nutzdaten": "payload data",
    "beteiligte": "participating",
    "feldstaerkemessung": "field strength measurement",
    "feldstaerke": "field strength",
    "feldstaerken": "field strengths",
    "schaltspielzaehler": "shift cycle counter",
    "startstopanforderungscode": "start-stop request code",
    "identifizierte": "identified", "rohkodierung": "raw coding",
    "schreibfreigabe": "write release", "einselne": "individual",
    "einzelne": "individual", "sonst": "other",
    "gefunden": "found", "kennline": "characteristic",
    "sollverbau": "target installation",
    "istverbau": "actual installation",
    "verbauzustand": "installation state",
    "datensatzdownload": "data set download",
    "testerdatumdatensatzdownload": "tester date data set download",
    "testerkennungdatensatzdownload":
        "tester identification data set download",
    "gespiegeltes": "mirrored", "getastet": "keyed",
    "verwendet": "used", "checksumme": "checksum",
    "regler": "controller", "regelbedarf": "control demand",
    "fuss": "foot", "ton": "tone", "toene": "tones",
    "normalsignal": "normal signal",
    "normiersignal": "normalization signal",
    "quittierungssignal": "acknowledge signal",
    "naeherungssensoren": "proximity sensors",
    "naeherungssensor": "proximity sensor",
    "naeherungssensorik": "proximity sensors",
    "codierzelle": "coding cell", "codierstelle": "coding location",
    "betriebssstatus": "operating status", "guete": "quality",
    "geschaetzte": "estimated",
    "speicherbefuelltest": "accumulator fill test",
    "speicherbefuelltests": "accumulator fill test",
    "lastkollektiv": "load collective",
    "hebebuehne": "vehicle lift",
    "beschlagsneigung": "fogging tendency",
    "standardwert": "standard value", "maske": "mask",
    "versionskennungen": "version identifiers",
    "versionsnummer": "version number",
    "parameterblockversion": "parameter block version",
    "knopfdruck": "button press", "systembedingt": "system-related",
    "sendet": "sends", "bedienteil": "control panel",
    "bedienteile": "control panels",
    "bedieninterface": "operating interface",
    "bedienmodi": "operating modes",
    "assistenzsysteme": "assistance systems",
    "verschleissintegrator": "wear integrator",
    "lamellen": "clutch plates",
    "lamellenarbeit": "clutch plate work", "kodier": "coding",
    "schliesstaster": "close button",
    "multimediasystem": "multimedia system",
    "uebersprechantenne": "crosstalk antenna",
    "uebersprechoffset": "crosstalk offset", "logik": "logic",
    "tiefniveau": "low level", "normalniveau": "normal level",
    "extremniveau": "extreme level",
    "niveaulage": "level position",
    "niveauregler": "level controller",
    "niveauaktuatorik": "level actuators",
    "anpasskanal": "adaptation channel",
    "anpasswert": "adaptation value",
    "umweltbedingungen": "environmental conditions",
    "einzelradgeschwindigkeit": "individual wheel speed",
    "undefiniert": "undefined", "zentrale": "central",
    "drehzahlgradient": "engine speed gradient",
    "resonanzfrequenz": "resonance frequency",
    "resonanzfrequenzen": "resonance frequencies",
    "essentielle": "essential", "verkabelung": "wiring",
    "sensorik": "sensors", "abstaende": "distances",
    "intervalltonbereich": "interval tone range",
    "sensorleiste": "sensor strip",
    "einklemmleiste": "anti-trap strip",
    "einklemmleisten": "anti-trap strips",
    "schaltleiste": "switch strip", "synchron": "synchronous",
    "synchronisierung": "synchronization",
    "authentikationsstatus": "authentication status",
    "spurverlassenswarnung": "lane departure warning",
    "spurwechselassistent": "lane change assist",
    "spurhalteassistent": "lane keeping assist",
    "servicedaten": "service data", "wartung": "service",
    "stahlfeder": "steel spring",
    "stahlfederung": "steel suspension",
    "justagezeitpunkt": "adjustment time",
    "dejustagewinkel": "misalignment angle",
    "zuruecksetzen": "reset",
    "dejustageerkennung": "misalignment detection",
    "ladegeaet": "charger", "ladegeraet": "charger",
    "wachhalten": "keep awake", "konditionierung": "conditioning",
    "gewaehlt": "selected", "ausgewaehlt": "selected",
    "ueberwachte": "monitored", "lehne": "backrest",
    "mechanischer": "mechanical",
    "unterdruckpumpe": "vacuum pump", "unterdruck": "vacuum",
    "unterdrucksensor": "vacuum sensor",
    "gespannstabilisierung": "trailer stabilization",
    "neuer": "new", "lenkuebersetzung": "steering ratio",
    "kopf": "head", "personenanstroemer": "person vent",
    "sportfunktion": "sport function",
    "sportabgasanlage": "sport exhaust system",
    "chipkartenleser": "chip card reader",
    "verkehrsdaten": "traffic data", "verkehrsinfo": "traffic info",
    "verkehrsart": "traffic type", "datenbank": "database",
    "lautstaerkeregler": "volume controller", "tiefe": "depth",
    "trennelement": "separating element",
    "geschwindingkeit": "speed", "neigungssensor": "tilt sensor",
    "gengstellersensor": "gear actuator sensor",
    "gangsteller": "gear actuator",
    "gangstellehaltdruck": "gear position holding pressure",
    "erwartungsfenster": "expectation window",
    "kompassgeraet": "compass unit",
    "schalttafel": "instrument panel", "uhr": "clock",
    "analog-uhr": "analog clock", "reifenpanne": "flat tire",
    "sensoreinbau": "sensor installation",
    "sensoreneinbaulage": "sensor installation position",
    "modus": "mode", "liegt nicht vor": "is not available",
    "liegt": "is", "zusatzvolumenventil": "additional volume valve",
    "ablassventil": "drain valve",
    "umschalteinheit": "switchover unit",
    "federbeindruck": "strut pressure",
    "bergabfahrassistent": "hill descent assist",
    "anfahrhilfe": "hill start aid",
    "multikollisionsbremse": "multi-collision brake",
    "anhaltewegverkuerzung": "stopping distance reduction",
    "rollsicherung": "roll protection",
    "anfahrmoment": "drive-off torque", "stufig": "stage",
    "hydraulische": "hydraulic",
    "ansteuerwert": "activation value",
    "frischluftgeblaese": "fresh air blower",
    "microphon": "microphone",
    "rueckspeisungen": "energy recoveries",
    "mindestzeit": "minimum time",
    "tastfunktion": "momentary function",
    "tippfunktion": "tip function",
    "ausloesesignal": "trigger signal", "einen": "a",
    "temperaturerfassung": "temperature sensing",
    "einsatzpunkt": "engagement point", "schon": "already",
    "durchgelaufen": "completed", "normalmodus": "normal mode",
    "benutzter": "used", "benutzten": "used",
    "schluesselsuch": "key search",
    "defaultgeschwindigkeit": "default speed",
    "verletzung": "violation",
    "tuergriffelektronik": "door handle electronics",
    "symbolik": "symbols", "sonderbereifung": "special tires",
    "reifenbeschreibung": "tire description",
    "fussgaengerwarnung": "pedestrian warning",
    "kamerasicht": "camera view", "unterstuetzt": "supported",
    "notbremswarnanzeige": "emergency brake warning display",
    "einspurmodell": "single-track model",
    "angetrieben": "driven", "gebremst": "braked",
    "freirollend": "free rolling", "ionisator": "ionizer",
    "testnachricht": "test message",
    "normalbetrieb": "normal operation",
    "hersteller": "manufacturer",
    "herstellerpruefstandsnummer":
        "manufacturer test bench number", "bindstrich": "hyphen",
    "applikationsversion": "application version",
    "inkompatible": "incompatible", "ausgekuppelt": "disengaged",
    "saugrohrunterdruck": "intake manifold vacuum",
    "radumfang": "wheel circumference", "generell": "general",
    "motoranlauf": "engine start", "zwingend": "absolutely",
    "notwendig": "necessary", "stoppverbot": "stop ban",
    "neutralsensor": "neutral sensor",
    "magnetkupplung": "magnetic clutch",
    "abwuergeschutz": "stall protection", "gekoppelt": "coupled",
    "heizungsabsperrventil": "heating shutoff valve",
    "federabsperrventil": "spring shutoff valve",
    "monostabil": "monostable",
    "wasserpumpennachlauf": "water pump afterrun",
    "temperaturvorwahl": "temperature preselection",
    "verbaupruefung": "installation check",
    "tastenkombination": "button combination",
    "jokerfunktion": "joker function",
    "umschalttaste": "switchover button",
    "neutralstellung": "neutral position",
    "lichtschema": "light scheme", "reserverad": "spare wheel",
    "schwellerbeleuchtung": "sill lighting",
    "obd-dose": "OBD socket", "umfeldleuchte": "surround lamp",
    "lernlauf": "learning run", "reversierung": "reversing",
    "aufgetreten": "occurred", "registriert": "registered",
    "verifiziert": "verified", "ungeschaerft": "disarmed",
    "schaerfezustand": "arming state",
    "alarmstatus": "alarm status", "alarmanlage": "alarm system",
    "anlass": "occasion",
    "erstinbetriebnahme": "first commissioning",
    "fahrerberechtigungskarten": "driver authorization cards",
    "sieben": "seven", "signalstaerke": "signal strength",
    "marke": "brand", "derivat": "derivative",
    "absatzmarkt": "sales market",
    "getriebeart": "transmission type",
    "doppelkupplungsgetriebe": "dual clutch transmission",
    "dampfdruckstufe": "vapor pressure stage",
    "unkritisch": "uncritical", "tankvolumen": "tank volume",
    "partikelfilter": "particulate filter", "groesse": "size",
    "ausbaustufe": "expansion stage", "doppelluefter": "dual fan",
    "stufenlos": "continuously variable",
    "offroadschalter": "offroad switch",
    "lenkungsart": "steering type",
    "kuehlerjalousie": "radiator shutter",
    "startzeitluecke": "start time gap",
    "linsenheizung": "lens heating",
    "blindheitserkennung": "blindness detection",
    "diebstahlschutz": "theft protection",
    "entwicklerbotschaften": "developer messages",
    "individualisierungsmerkmal": "individualization feature",
    "fahrzeuguebergreifend": "cross-vehicle",
    "parkhilfe": "park assist",
    "zusatzinformation": "additional information",
    "innerer": "inner", "pro": "per",
    "ausschaltgeschwindigkeitsschwelle":
        "switch-off speed threshold", "fehlerton": "fault tone",
    "quittierton": "acknowledge tone", "demoton": "demo tone",
    "ruecknahme": "withdrawal", "angezogener": "applied",
    "manueller": "manual", "manuellem": "manual",
    "schwenkbarer": "swiveling",
    "mittelkritische": "medium-critical",
    "hindernisse": "obstacles",
    "vierradantrieb": "four-wheel drive",
    "laendercharakteristik": "country characteristics",
    "dauertonausweitung": "continuous tone extension",
    "targaparameter": "Targa parameters",
    "plausibilitaet": "plausibility",
    "diagnosezugang": "diagnostic access",
    "heckradar": "rear radar", "soundaktor": "sound actuator",
    "zweiseitig": "two-sided", "motorsensorik": "motor sensors",
    "fangbereich": "catch range",
    "flankenabhaengigkeit": "edge dependency",
    "produktionsmode": "production mode",
    "reversier": "reversing", "inkremente": "increments",
    "angesteuert": "activated", "greifpunkt": "grip point",
    "eingelaufen": "run in", "toleranzband": "tolerance band",
    "welle": "shaft", "schema": "scheme",
    "bevorzugter": "preferred",
    "einschaltrampe": "switch-on ramp",
    "ruecksetzmodus": "reset mode",
    "quittierung": "acknowledgment",
    "ausgefuehrte": "executed",
    "notlaeufe": "limp mode events", "einmalige": "one-time",
    "gruen": "green", "russindex": "soot index",
    "ausblenden": "hide",
    "verbrennungsmotor": "combustion engine",
    "vorgluehen": "glow plug preheating",
    "umluftreinigung": "recirculation cleaning",
    "abscheidevolumen": "separation volume",
    "erschoepft": "exhausted", "kollisions": "collision",
    "motorkennbuchstabe": "engine code letters",
    "gestartet": "started",
    "dieselpartikelfilter": "diesel particulate filter",
    "motorstop": "engine stop",
    "bremskraftverstaerkung": "brake force boosting",
    "auffuellen": "refill", "abgebrochen": "aborted",
    "blockiert": "blocked", "nachfuellmenge": "refill quantity",
    "kugelrampe": "ball ramp",
    "verteilergetriebe": "transfer case",
    "ueberschnapper": "overshoot",
    "federratenmodus": "spring rate mode",
    "wagenhebermodus": "jack mode", "aufwaerts": "upward",
    "statusbyte": "status byte", "routinestatus": "routine status",
    "fahrbereitschaft": "readiness to drive",
    "fahrzeugverschraenkung": "vehicle articulation",
    "verschraenkung": "articulation", "ungefiltert": "unfiltered",
    "vollstaendiger": "complete",
    "luftmengenmessung": "air quantity measurement",
    "befuellung": "filling", "konzernindex": "group index",
    "konzern": "group", "basisradstand": "base wheelbase",
    "achs": "axle", "stadt": "city", "aktuatorik": "actuators",
    "kreuzungslicht": "intersection light",
    "mitleuchtend": "co-illuminating",
    "nebellichtunterstuetzung": "fog light support",
    "schlechtwetterlicht": "bad weather light",
    "starken": "strong", "ecomodus": "eco mode",
    "spannungserfassung": "voltage sensing",
    "sichergestellt": "ensured", "abgeschlossen": "completed",
    "ausserordentlich": "extraordinarily", "bereits": "already",
    "lichtaktor": "light actuator", "gegeben": "given",
    "gemessen": "measured", "gemessener": "measured",
    "eingelesen": "read in", "positiv": "positive",
    "leichtbau": "lightweight", "entluefung": "venting",
    "hebebuehnenregler": "lift controller",
    "serviceregler": "service controller",
    "schiefstandregler": "tilt controller",
    "achsregler": "axle controller",
    "druckausgleich": "pressure equalization",
    "fahrzeugschiefstand": "vehicle tilt",
    "fehlerquelle": "fault source",
    "umrechnungsfaktor": "conversion factor",
    "bremst": "braking", "spaet": "late",
    "servicevertrag": "service contract", "gong": "chime",
    "wachhaltezaehler": "keep-awake counter",
    "grosse": "large",
    "frontscheibenheizung": "windshield heating",
    "maskierung": "masking", "ueberlauf": "overflow",
    "lichtverteilung": "light distribution",
    "oelservice": "oil service", "entprellung": "debouncing",
    "haelt": "holds", "abwaerts": "downward",
    "projektindividuell": "project-specific",
    "mess-can": "measuring CAN", "anzeigetext": "display text",
    # dotted abbreviations found in this sweep
    "schliessbew.": "closing movement", "anz.": "number of",
    "zeitl.": "temporal", "entw.": "development",
    "betaet.": "actuation", "oeltemp.": "oil temp",
    # dotted German abbreviations (may be glued to the next word)
    "akt.": "current", "aussenbel.": "exterior lighting",
    "bremsl.": "brake light", "prod.": "production",
    "verd.": "top", "bed.": "operating", "man.": "manual",
    "unbek.": "unknown", "unbel.": "unloaded", "inkl.": "incl.",
    "u.": "and", "f.": "for", "d.": "of the", "geschw.": "speed",
    "pos.": "position", "akust.": "acoustic", "bzw.": "or",
    "fussg.": "pedestrian", "elekr.": "electr.", "elektr.": "electr.",
    "klimakomp.": "climate compressor",
}

# --- VALUE contents (exact match on the normalized string) -----------------
TRANS_VALUE = {
    "ja": "yes", "Ja": "Yes", "nein": "no", "Nein": "No",
    "nicht aktiv": "not active", "aktiv": "active", "Aktiv": "Active",
    "inaktiv": "inactive", "Inaktiv": "Inactive", "aus": "off",
    "ein": "on", "Ein": "On", "an": "on", "deaktiv": "deactivated",
    "verbaut": "installed", "unverbaut": "not installed",
    "nicht verbaut": "not installed", "Nicht verbaut": "Not installed",
    "keine verbaut": "none installed",
    "Kein Fehler erkannt": "No fault detected",
    "Kein Fehler": "No fault", "kein Fehler": "no fault",
    "Kommunikation gestoert": "Communication disturbed",
    "Kommunikation in Ordnung": "Communication OK",
    "Zuendkreis deaktiviert": "Ignition circuit deactivated",
    "Endstufe nicht fuer die eingestellte Dauer durchgeschaltet.":
        "Output stage not switched through for the configured duration.",
    "Zuendkreisansteuerung 1.75A/1ms":
        "Ignition circuit activation 1.75A/1ms",
    "Ersatzmassnahme nicht aktiv": "Substitute measure not active",
    "KEINE WARNUNG": "NO WARNING",
    "keine Warnung vorhanden": "no warning present",
    "nicht angelernt": "not learned",
    "nicht betaetigt": "not pressed", "betaetigt": "pressed",
    "unbetaetigt": "not pressed", "kein Eintrag": "no entry",
    "Botschaft aktuell": "Message current",
    "Signal gueltig": "Signal valid",
    "Gurtschloss nicht gesteckt": "Belt buckle not latched",
    "nicht gesteckt": "not latched",
    "Diagnose nicht abgeschlossen": "Diagnosis not completed",
    "normiert": "normalized", "nicht normiert": "not normalized",
    "Initwert": "Initial value", "Initialwert": "Initial value",
    "Defaultwert": "Default value",
    "Beifahrerairbag deaktiviert": "Passenger airbag deactivated",
    "Position erkannt": "Position detected",
    "Ganganzeige leer": "Gear display empty",
    "Schluessel 1": "Key 1", "Geprueft i.O": "Checked OK",
    "Freigabe": "Release", "mittel": "medium",
    "Paniktaste Schluessel (nur USA)": "Panic button key (USA only)",
    "Grau": "Gray", "grau": "gray", "Gelb": "Yellow", "gelb": "yellow",
    "Rot": "Red", "rot": "red", "weiss": "white",
    "kein Fehlereintrag vorhanden": "no fault entry present",
    "Sitz nicht belegt": "Seat not occupied",
    'Position "Vorne" erkannt': 'Position "front" detected',
    "automatisch": "automatic",
    "35 Akustik Gurtwarnung": "35 acoustic belt warning",
    "Vollerkennung": "Full detection",
    "Kein Objekt im Messbereich": "No object in measuring range",
    "Halbschritt": "Half step", "Mikroschritt": "Microstep",
    "Slave nicht verbaut": "Slave not installed",
    "Linkslenker": "Left-hand drive",
    "Rechtslenker": "Right-hand drive",
    "verfuegbar": "available", "Kein Schreibauftrag": "No write job",
    "Lampe aus": "Lamp off",
    "Airbag nicht in Diagnose": "Airbag not in diagnosis",
    "kein KD-Fehler": "no KD fault", "Keine Anzeige": "No display",
    "keine Anzeige": "no display",
    "keine Anzeige/ Leerzeile": "no display / blank line",
    "Kein qualifizierter Fehler vorhanden.":
        "No qualified fault present.",
    "Kein Crash": "No crash", "Fehlereintrag": "Fault entry",
    "Drehmomentanzeige": "Torque display", "Fahrzeug": "Vehicle",
    "Die letzte Programmierung war erfolgreich":
        "The last programming was successful",
    "ungueltig (noch kein Kalibriervorgang im Kl. 15 - Zyklus)":
        "invalid (no calibration yet in the Kl. 15 cycle)",
    "erfuellt": "fulfilled", "nicht erfuellt": "not fulfilled",
    "nicht belegt": "not assigned",
    "Block programmiert und kompatibel":
        "Block programmed and compatible",
    "Restreichweite und Tankanzeige": "Remaining range and fuel gauge",
    "Uhrzeit und Temperatur": "Time and temperature",
    "Fahrtrichtung": "Direction of travel",
    "nach Anfahren": "after driving off", "MM/TT/JJJJ": "MM/DD/YYYY",
    "US-Englisch": "US English",
    "KI-BC-Seite Fahrzeug": "Cluster BC page, vehicle",
    "Oeltemperatur": "Oil temperature", "Oeldruck": "Oil pressure",
    "Kuehlwassertemperatur": "Coolant temperature",
    "Nachtankereignis OK": "Refueling event OK",
    "Tipptaste nicht betaetigt": "Momentary button not pressed",
    "volle Funktion": "full function", "nicht erkannt": "not detected",
    "gespannt": "tensioned",
    "normaler Sendemode": "normal transmit mode",
    "Ungueltige Satelliten-ID": "Invalid satellite ID",
    "LED aus": "LED off", "LED ein": "LED on",
    "Zweiradantrieb": "Two-wheel drive", "gueltig": "valid",
    "ungueltig": "invalid", "gueltiger Wert": "valid value",
    "ungueltiger Wert": "invalid value",
    "ungueltiger Schluessel": "invalid key", "offen": "open",
    "FFB auf": "FFB unlock", "FFB zu": "FFB lock",
    "FFB Deckel hinten / Heckklappe": "FFB rear lid / tailgate",
    "Fahrertuer": "Driver door", "Gang 1": "Gear 1", "Gang 2": "Gear 2",
    "1. Gang": "1st gear",
    "Reserve wurde erkannt": "Reserve was detected",
    "Benzin": "Gasoline", "Benziner": "gasoline engine",
    "Stillstand": "Standstill", "vorne links": "front left",
    "vorne rechts": "front right", "hinten links": "rear left",
    "hinten rechts": "rear right", "unten rechts": "bottom right",
    "keine HF-Ueberdeckung": "no RF coverage", "keiner": "none",
    "einfahren": "retract", "SG codiert": "ECU coded",
    "Leuchte wird abgeschaltet": "Lamp is switched off",
    "PTC Heizung": "PTC heater",
    "Motor 4 Limo vorn": "Motor 4 sedan front",
    "FH steht": "Window lifter stopped",
    "kein Messwert": "no measured value", "auf": "open",
    "verriegelt": "locked", "entriegelt": "unlocked",
    "nicht erlaubt": "not permitted", "erlaubt": "permitted",
    "Key-Taste": "Key button",
    "Memory abschalten wegen Off": "Switch off memory due to Off",
    "nicht gedrueckt": "not pressed",
    "bei langsamer Fahrt eingelernt, fertig eingelernt":
        "learned at low speed, fully learned",
    "Unterbrechung": "Open circuit", "Sitz ist leer": "Seat is empty",
    "Sitzpsition Hinten": "Seat position rear",
    "Bremse geschlossen": "Brake closed",
    "kein Bremsen": "no braking",
    "Kommunikation mit Vorderwagenelektronik":
        "Communication with front-end electronics",
    "Diagnose nicht unterstuetzt": "Diagnosis not supported",
    "Relais i.O.": "Relay OK", "alle Tueren": "all doors",
    "Bordspannung": "On-board voltage", "GPS-Hoehe": "GPS altitude",
    "Negativ": "Negative",
    "nicht angeschlossen": "not connected",
    "angeschlossen": "connected",
    "136 Reichweite (Tankinhalt)": "136 range (tank content)",
    "26 Gurtwarnung": "26 belt warning",
    "141 Service-Intervall-Anzeige": "141 service interval display",
    "Statistische Plausibilisierung fehlgeschlagen":
        "Statistical plausibility check failed",
    "passive Magnetstellung": "passive magnet position",
    "Sensor nicht gedreht": "Sensor not rotated",
    "kalibriert": "calibrated",
    "Variante verfuegbar": "Variant available", "Heck": "Rear",
    "Schwelle 1": "Threshold 1", "EVB ein": "EVB on",
    "HBA nicht durch ACC getriggert": "HBA not triggered by ACC",
    "EVB nicht durch ACC getriggert": "EVB not triggered by ACC",
    "DST ein": "DST on",
    "Anfahrassistent HOLD/Hillhold an":
        "Hill start assist HOLD/hillhold on",
    "ACC aus": "ACC off", "keine Ansteuerung": "no activation",
    "Bremse nicht betaetigt": "Brake not pressed",
    "Stahl": "Steel", "Klemme 15": "Terminal 15",
    "kein Hybridfahrzeug": "no hybrid vehicle",
    "geoeffnet": "opened",
    "VW Wandlerautomat": "VW torque converter automatic",
    "Einschleifen nicht gestartet und System kalibriert":
        "Grinding-in not started and system calibrated",
    "DAR nicht verfuegbar, Fahrertuer offen, Fahrer nicht angeschnallt":
        "DAR not available, driver door open, driver not buckled",
    "DAR nicht verfuegbar, Fahrer nicht angeschnallt":
        "DAR not available, driver not buckled",
    "SG-Nachlauf": "ECU afterrun", "RdW": "RoW (rest of world)",
    "Lernphase laeuft": "Learning phase in progress",
    "Fahrzeug steht": "Vehicle stationary",
    "CAN-Aktivitaet": "CAN activity",
    "VERDECK SYNC-SG": "CONVERTIBLE TOP SYNC ECU",
    "Datumsinformation fehlerhaft": "Date information faulty",
    "Kilometerstand fehlerhaft": "Odometer reading faulty",
    "Verdeck in Endlage (geoeffnet)":
        "Convertible top in end position (opened)",
    "DK verbaut": "DK installed",
    "EC-Spiegel verbaut": "EC mirror installed",
    "Innenraumueberwachung verbaut": "Interior monitoring installed",
    "Kontakt Handschuhkasten": "Glovebox contact",
    "DriveDown (Das System ist bereit, Lenkunterstuetzung kann "
    "zugeschaltet werden)":
        "DriveDown (system ready, steering assist can be engaged)",
    "KL15 ein": "KL15 on", "unplausibel": "implausible",
    "Steuergeraet programmiert und verriegelt.":
        "Control unit programmed and locked.",
    "Nicht vorhanden": "Not present",
    "Subbusystem Tastenfeld verbaut": "Sub-bus system keypad installed",
    "Sportabgas": "Sport exhaust",
    "Verdeck oeffnen": "Open convertible top",
    "Verdeck schliessen": "Close convertible top",
    "nicht getoent": "not tinted",
    "eigene Codierung verwenden": "use own coding",
    "Automatikgetriebe": "Automatic transmission",
    "Tasten": "Buttons", "Komfort-Memory": "Comfort memory",
    "saubere Luft": "clean air", "zentr Gateway": "central gateway",
    "Sauger": "naturally aspirated", "sperren": "lock",
    "Endlage eingefahren erreicht": "End position retracted reached",
    "Beide Sensorwerte nicht im gueltigen Bereich/ ungueltig.":
        "Both sensor values not in valid range / invalid.",
    "Abgleichswerte OffsetFront und OffsetRear sind gueltig.":
        "Calibration values OffsetFront and OffsetRear are valid.",
    "kalibrierte Hoehenwerte in mm": "calibrated height values in mm",
    "USA / Kanada": "USA / Canada",
    "USA. Kanada und US-Aussenterritorien":
        "USA, Canada and US outlying territories",
    "zwei": "two", "vorwaerts": "forward",
    "es wurde keine Taste gedrueckt seit Power On":
        "no button pressed since power on",
    "Fehler": "Fault", "9x1 Sportwagen": "9x1 sports car",
    "Verbundrelease / Serienstand": "Network release / production level",
    "Produktions-BIN": "Production BIN",
    "Startfreigabe in P": "Start release in P",
    "keine Anforderung": "no request",
    "Kein ESP Eingriff": "No ESP intervention",
    "ESP aktiviert": "ESP activated", "kein Kickdown": "no kickdown",
    "Programm 2": "Program 2",
    "nicht verbaut oder kein ACC zulaessig":
        "not installed or no ACC permitted",
    "Faktor: x 1,0Nm": "Factor: x 1.0 Nm",
    "keine Schaltung aktiv": "no shift active",
    "Kupplung geoeffnet": "Clutch open",
    "Ventil TMM oder geschlossen": "Valve TMM or closed",
    "Kein Notlauf": "No limp mode",
    "Kalibrierung durch Produktion erfolgt":
        "Calibration done by production",
    "Kein Signal": "No signal",
    "Funkschluessel 1 aktiv": "Radio key 1 active",
    "leiser": "quieter", "abwaerts": "downward",
    "Wischerstellung 0": "Wiper position 0",
    "Intervallstellung 1": "Intermittent position 1",
    "Neutral-Stellung": "Neutral position",
    "Tempomat-Taste unbetaetigt": "Cruise control button not pressed",
    "Stellung offen / unbetaetigt": "Position open / not pressed",
    "LHZ-Taster nicht betaetigt": "LHZ button not pressed",
    "Stoppuhr": "Stopwatch",
    "Kommunikation mit Steuergeraet-PSM":
        "Communication with PSM control unit",
    "Kommunikation mit Steuergeraet Getriebe":
        "Communication with transmission control unit",
    "Fehler im Ausloesegeraet POSIP/Airbag":
        "Fault in deployment unit POSIP/airbag",
    "DME Steuergeraet defekt: Reset":
        "DME control unit defective: reset",
    "Fehlerspeicherinhalt Steuergeraet-PSM pruefen":
        "Check fault memory of PSM control unit",
    "Ansteuerung Starterrelais": "Starter relay activation",
    "Lambdasonde vor Kat Bank 1 - Dynamik":
        "Oxygen sensor upstream of catalyst, bank 1 - dynamics",
    "Lambda-Sonde vor Kat Bank 2 - Dynamik":
        "Oxygen sensor upstream of catalyst, bank 2 - dynamics",
    "Diagnose ohne Fehler beendet":
        "Diagnosis completed without faults",
    "Kurztest bereit": "Short test ready",
    "WWS verbaut": "WWS installed", "RLS verbaut": "RLS installed",
    "LDS verbaut": "LDS installed", "UGDO verbaut": "UGDO installed",
    "LV verbaut": "LV installed", "Start aktiv": "Start active",
    "ELV-Position unbekannt": "ELV position unknown",
    "Hybrid nicht aktiviert": "Hybrid not activated",
    "REK aktiviert": "REK activated",
    "erweiterte REK nicht aktiviert": "extended REK not activated",
    "Segeln aktiviert": "Coasting activated",
    "Start/Stopp aktiviert": "Start/stop activated",
    "Batterie ist angeschlossen": "Battery is connected",
    "Funktion nicht aktiv": "Function not active",
    "Ungueltige EEPROMDaten (Checksummenfehler)":
        "Invalid EEPROM data (checksum error)",
    "Es ex. weitere Fehler, die zur Nichtauswertbarkeit fuehren":
        "Further faults exist that prevent evaluation",
    "HauptSich.Box": "Main fuse box",
    "Abschaltung Kl30f moeglich": "Switch-off of Kl30f possible",
    "Kl.15 diskret = Kl.15 LIN": "Kl.15 discrete = Kl.15 LIN",
    "keine Fremdladung erkannt": "no external charging detected",
    "Kl.30f Relais ein": "Kl.30f relay on",
    "Kl.30sd Relais ein": "Kl.30sd relay on",
    "HSB im Fahrbetrieb": "HSB in driving operation",
    "HSB nicht im Transportmode": "HSB not in transport mode",
    "Einfuehrungsmodell": "Launch model",
    "Tankvariante_1": "Tank variant_1",
    "Skalenbeleuchtung alt (Standlichtkontrollleuchte als "
    'Fahrlichtkontrollleuchte "Abblendlicht")':
        "Scale illumination old (standing light indicator as driving "
        'light indicator "low beam")',
    'Reifenumfang 19"': 'Tire circumference 19"',
    "PT1 Fototransistordaempfung": "PT1 phototransistor damping",
    "lineare Fototransistordaempfung":
        "Linear phototransistor damping",
    "links": "left", "rechts": "right",
    "Ruecksetzen bei Tastenbedienung": "Reset on button operation",
    "Sommerreifen": "Summer tires", "19 Zoll": "19 inch",
    "Teilbeladung": "Partial load", "Komfortdruck": "Comfort pressure",
    "Schwelle 270 km/h": "Threshold 270 km/h",
    "Anzeigetext": "Display text",
    "Mehr Verkehrszeichen": "More traffic signs",
    "Klemme S an": "Terminal S on",
    "Bargraf skaliert": "Bar graph scaled",
    "Grafikdarstellung klein": "Graphic display small",
    "196 PSM in Diagnose": "196 PSM in diagnosis",
    "116 Parkbremse im Servicemodus":
        "116 parking brake in service mode",
    "265 Schluesselwarnung Akustik": "265 key warning acoustic",
    "82 Bitte den Waehlhebel in P":
        "82 please put the selector lever in P",
    "Nachtankmodus": "Refueling mode",
    "Verbrauchsrueckrechnung": "Consumption back-calculation",
    "Externer Photosensor": "External photosensor",
    "Beschleunigungssensor ADXL 180; 100 g":
        "Acceleration sensor ADXL 180; 100 g",
    "Beschleunigungssensor ADXL 180; 250 g":
        "Acceleration sensor ADXL 180; 250 g",
    "Drucksensor KP106; -2.4 .. 11.0 %":
        "Pressure sensor KP106; -2.4 .. 11.0 %",
}

# --- units -----------------------------------------------------------------
TRANS_UNIT = {
    "Anzahl": "count", "Zyklen": "cycles", "Zaehler": "counter",
    "Versuche": "attempts", "Betaet.": "actuations", "sek": "sec",
}

# --- control unit titles: English gloss shown next to the original ---------
ECU_GLOSS = {
    "WH": "selector lever",
    "PASM+PADM 9x1 a2.1": "adaptive suspension (PASM+PADM)",
    "PSM_a2": "stability management (PSM)",
    "Parkbremse_a7": "parking brake",
    "Sensorcluster": "sensor cluster",
    "Reifendruckkontrolle_A2_4": "tire pressure monitoring",
    "Verdeck Synchronisations-SG A1.0":
        "convertible top synchronization ECU",
    "BCM hinten 9x1 VR12": "body control module rear",
    "IRUE": "interior monitoring",
    "EC-Spiegel": "electrochromic mirror",
    "Dachkonsole": "roof console",
    "EPS_A2_2": "electric power steering",
    "Tuer_Vorne_A2_2 (Tuer vorne rechts)": "front door, right",
    "Tuer_Vorne_A2_2 (Tuer vorne links)": "front door, left",
    "Sitz_A2.1_9x1 (Sitz Beifahrer)": "passenger seat",
    "Sitz_A2.1_9x1 (Sitz Fahrer)": "driver seat",
    "Airbag_A2.6_9x1": "airbag",
    "PODS Insassenerkennung": "PODS occupant detection",
    "BKE_2_Zonen_A2_3": "climate control (2-zone)",
    "BKE-Tastenfeld": "climate control keypad",
    "PDC_8_Kanal_A2_4": "park distance control (8-channel)",
    "SWSG_Master_mit_AFS_ab_VR06 (Scheinwerfer links)":
        "headlight ECU master with AFS, left headlight",
    "SWSG_Slave_mit_AFS_ab_VR06 (Scheinwerfer rechts)":
        "headlight ECU slave with AFS, right headlight",
    "PCM31 A3.3": "Porsche Communication Management",
    "PDK A3 ohne Quersperre": "PDK transmission, no differential lock",
    "Kombilenkstockmodul_A2_5": "steering column stalk module",
    "MFL": "multifunction steering wheel",
    "Zusatzinstrument_A2.1": "auxiliary instrument",
    "SDI9.1 981 3,4L ULEV": "engine control unit (DME)",
    "BCM vorne 9x1 Basis VR12": "body control module front",
    "Lichtdrehschalter": "headlight rotary switch",
    "Wischwinkelsteuerung": "wiper angle control",
    "Regen-Licht-Feuchte-Sensor": "rain/light/humidity sensor",
    "Lenksaeulenverstellung": "steering column adjustment",
    "UGDO": "universal garage door opener",
    "CAN/CAN-Gateway A4.2": "CAN gateway",
    "Intelligenter Batteriesensor": "intelligent battery sensor",
    "Hauptsicherungsbox": "main fuse box",
    "DC/DC-Wandler": "DC/DC converter",
    "Generator": "alternator",
    "Kombiinstrument_A2.8": "instrument cluster",
    # --- Macan (95B / CajunII) control units ---
    "Stahlfederung Colorado mit PASM_a2.3": "steel suspension with PASM",
    "SCHEINWERFER_4": "headlight",
    "SCHEINWERFER_LEIMO_RECHTS": "headlight power module, right",
    "SCHEINWERFER_LEIMO_LINKS": "headlight power module, left",
    "SCHEINWERFER_LED_LINKS": "LED headlight, left",
    "SCHEINWERFER_LED_RECHTS": "LED headlight, right",
    "EV_ESP_10001": "stability control (ESP)",
    "Zwei_Drei_Zonen_Climatronic_A2.45":
        "climate control (2/3-zone Climatronic)",
    "Bedienteil Slave": "control panel, slave",
    "SWA2_A2_1": "lane change assist",
    "Lane_change_assistant 2": "lane change assist",
    "RCevo_PO416_UKR_002": "reversing camera",
    "Kombilenkstockmodul_A1_2": "steering column stalk module",
    "BCM hinten E2 GT5": "body control module rear",
    "Sounder": "alarm sounder",
    "Sonnenrollo (Dach)": "sun blind (roof)",
    "Panorama-/Grossdach": "panoramic/large roof",
    "VTS": "vehicle tracking system",
    "Tuer_Hinten_A2_2 (Tuer hinten rechts)": "rear door, right",
    "Tuer_Hinten_A2_2 (Tuer hinten links)": "rear door, left",
    "Sitz_A2.1_E2 (Sitz Fahrer)": "driver seat",
    "Sitz_A2.1_E2 (Sitz Beifahrer)": "passenger seat",
    "RealTopView2_009": "surround view camera",
    "ECM 30TFS 011 95B 907 559E 001": "engine control module",
    "AMP Mst 16C4 Gen2 BOSE 002": "BOSE amplifier",
    "ACC3_A7": "adaptive cruise control",
    "MU Hig Gen2plus ALPI": "media unit (PCM)",
    "Centerdisplay": "center display",
    "Navi - Datenbank": "navigation database",
    "PDC_8_Kanal_A2_7": "park distance control (8-channel)",
    "Scheinwerfer_LED_DK2F_001 (Scheinwerfer LED rechts)":
        "LED headlight, right",
    "Scheinwerfer_LED_DK2F_001 (Scheinwerfer LED links)":
        "LED headlight, left",
    "CAN/CAN-Gateway A7.1": "CAN gateway",
    "Heckklappe A2_7": "tailgate",
    "TCM DL 501 021": "transmission control module",
    "EV AirbaVW10BPAVW526 002": "airbag",
    "PODS": "occupant detection",
    "Zusatzinstrument_A2.3": "auxiliary instrument",
    "BCM vorne E2 Max GT5": "body control module front",
    "Kombiinstrument_Gen2_A4.2": "instrument cluster",
    "MFK Bosch PO416": "multifunction camera",
    "RDK3_007": "tire pressure monitoring",
    "Allrad_A2.3": "all-wheel drive",
    "Trailer Function Generation 2 Hella": "trailer function",
}

# --- translation engine ----------------------------------------------------
import re as _re

_SEG_SPLIT = _re.compile(r"(\s*:\s+|\s+-\s+)")
_DIGITS = _re.compile(r"\d+")
# Keys ending in '.' are abbreviations and may be glued directly to the
# next word ("Verd.oeffnen"), so they get no trailing boundary check.
_DOTTED = sorted((k for k in TRANS_PHRASE if k.endswith(".")),
                 key=len, reverse=True)
_PLAIN = sorted((k for k in TRANS_PHRASE if not k.endswith(".")),
                key=len, reverse=True)
_PHRASE_RE = _re.compile(
    "(?<![A-Za-z])(" + "|".join(_re.escape(k) for k in _DOTTED)
    + ("|" if _DOTTED else "")
    + "(?:" + "|".join(_re.escape(k) for k in _PLAIN)
    + ")(?![A-Za-z]))",
    _re.IGNORECASE)
_LABEL_CACHE = {}
_VALUE_CACHE = {}

# Extra stems used only for compound-word decomposition (never replaced
# standalone). Add here when a compound part is too ambiguous or too
# productive to be a whole-word TRANS_PHRASE entry.
_EXTRA_STEMS = {
    "fahr": "driving", "zuend": "ignition", "heiz": "heating",
    "kuehl": "cooling", "brems": "brake", "schalt": "shift",
    "steuer": "control", "regel": "control", "wisch": "wipe",
    "wasch": "wash", "lade": "charge", "mess": "measuring",
    "pruef": "test", "warn": "warning", "anzeig": "display",
    "verstell": "adjustment", "sperr": "lock", "dreh": "rotation",
    "blink": "flash", "leucht": "light", "senk": "lower",
    "heb": "lift", "weg": "travel", "zahl": "count",
    "ablage": "storage", "fixierung": "securing",
    "halterung": "mount", "halter": "holder", "traeger": "carrier",
    "kontakt": "contact", "schutz": "protection",
    "wechsel": "change", "freigabe": "release",
    "begrenzung": "limiting", "erhoehung": "increase",
    "absenkung": "lowering", "anhebung": "raising",
    "oeffnung": "opening", "schliessung": "closing",
    "meldung": "message", "kennzeichnung": "marking",
    "zuordnung": "assignment", "abfrage": "query",
    "vorgabe": "specification", "grenze": "limit",
    "punkt": "point", "feld": "field", "gruppe": "group",
    "satz": "set", "folge": "sequence", "lauf": "run",
    "luft": "air", "wasser": "water", "oel": "oil",
    "sonde": "probe", "takt": "cycle", "hinter": "rear",
    "vorder": "front", "panorama": "panoramic", "werk": "factory",
    "heck": "rear", "last": "load", "stell": "adjustment",
    # identity stems: same in English, listed so compounds decompose
    # ("Lenkwinkelsensor" -> steering angle + sensor)
    "sensor": "sensor", "motor": "motor", "status": "status",
    "system": "system", "info": "info", "test": "test",
    "signal": "signal", "filter": "filter", "parameter": "parameters",
    "modus": "mode", "elektronik": "electronics",
    "management": "management", "manager": "manager",
    "logger": "logger", "code": "code", "bus": "bus",
    "slave": "slave", "master": "master", "display": "display",
    "memory": "memory", "phototransistor": "phototransistor",
    "skala": "scale", "methode": "method", "zone": "zone",
    "ziel": "target", "haupt": "main", "neben": "secondary",
    "sonder": "special", "ober": "upper", "gesamt": "total",
    "kalibrier": "calibration", "detektion": "detection",
    "spitze": "peak", "spitzen": "peak",
}

# Words that must never be used as compound parts (function words,
# separable prefixes whose composed translation would be misleading).
_STEM_EXCLUDE = {
    "und", "oder", "nicht", "ohne", "fuer", "wenn", "weil", "alle",
    "eine", "beim", "ueber", "unter", "zwischen", "wegen", "durch",
    "der", "die", "das", "des", "dem", "den", "ist", "war", "wird",
    "sind", "nur", "als", "ein", "aus", "auf", "von", "vor", "nach",
    "bei", "mit", "seit", "immer", "einmal", "welcher", "zurueck",
    "groesser", "gleich", "anzahl der",
}

_STEMS = dict(_EXTRA_STEMS)
for _k, _v in TRANS_PHRASE.items():
    if _k.isalpha() and len(_k) >= 3 and _k not in _STEM_EXCLUDE:
        _STEMS[_k] = _v


def _decompose(word, depth=0):
    """Split an unknown lowercase word into known German stems (allowing
    the linking elements s/es/n/en/e between parts). Returns the joined
    English translation, or None if no full decomposition exists."""
    if depth > 3 or len(word) < 6:
        return None
    for end in range(len(word) - 3, 2, -1):  # longest prefix first
        eng = _STEMS.get(word[:end])
        if eng is None:
            continue
        rest = word[end:]
        for link in ("", "s", "es", "n", "en", "e"):
            if not rest.startswith(link):
                continue
            r2 = rest[len(link):]
            if len(r2) < 3:
                continue
            sub = _STEMS.get(r2)
            if sub is None:
                sub = _decompose(r2, depth + 1)
            if sub:
                return eng + " " + sub
    return None


_TOKEN_RE = _re.compile(r"[A-Za-z]{6,}")


def _underscore_style(m, eng):
    """Join a multi-word English replacement with underscores when the
    matched word sits inside an underscore-joined identifier."""
    s, e, txt = m.start(), m.end(), m.string
    if (s > 0 and txt[s - 1] == "_") or \
       (e < len(txt) and txt[e] == "_"):
        return "_".join(eng.split())
    return eng


def _phrase_pass(seg):
    # pass 1: known words and phrases, whole-word
    def _known(m):
        eng = _underscore_style(m, TRANS_PHRASE[m.group(0).lower()])
        # a dotted abbreviation may be glued to the next word
        # ("Verd.oeffnen"); keep a space in the English
        if m.group(0).endswith(".") and m.end() < len(m.string) \
                and m.string[m.end()].isalpha():
            eng += " "
        return eng
    out = _PHRASE_RE.sub(_known, seg)

    # pass 2: decompose remaining long words into known compound stems.
    # Inside underscore-joined identifiers the English words are joined
    # with underscores to preserve the identifier style.
    def _try_compound(m):
        word = m.group(0)
        lw = word.lower()
        dec = _STEMS.get(lw) or _decompose(lw)
        if not dec:
            # retry without a trailing plural/genitive ending
            # ("Kuehlmittelwarnungs" -> "Kuehlmittelwarnung")
            for suf in ("es", "en", "s", "n", "e"):
                if lw.endswith(suf) and len(lw) - len(suf) >= 6:
                    base = lw[:-len(suf)]
                    dec = _STEMS.get(base) or _decompose(base)
                    if dec:
                        break
        if not dec:
            return word
        return _underscore_style(m, dec)

    out = _TOKEN_RE.sub(_try_compound, out)
    # collapse doubled articles from stacked replacements
    # ("in der die ..." -> "in of the the ..." -> "in the ...")
    out = _re.sub(r"\b(?:of the|the) the\b", "the", out)
    if out != seg and seg[:1].isupper() and out[:1].islower():
        out = out[0].upper() + out[1:]
    return out


def _translate_segment(seg):
    if not seg:
        return seg
    key = _DIGITS.sub("#", seg)
    tmpl = TRANS_SEGMENT.get(key)
    if tmpl is not None:
        nums = _DIGITS.findall(seg)
        if tmpl.count("#") == len(nums):
            for n in nums:
                tmpl = tmpl.replace("#", n, 1)
            return tmpl
    return _phrase_pass(seg)


def translate_text(text, exact_first=None):
    """Translate a normalized label/value; returns the English (or partly
    translated, or unchanged) normalized string."""
    norm = norm_de(text)
    if not norm:
        return norm
    if exact_first is not None:
        hit = exact_first.get(norm)
        if hit is not None:
            return hit
    hit = TRANS_LABEL.get(norm)
    if hit is not None:
        return hit
    parts = _SEG_SPLIT.split(norm)
    for i in range(0, len(parts), 2):
        parts[i] = _translate_segment(parts[i])
    return "".join(parts)


def translate_label(text):
    norm = norm_de(text)
    if norm not in _LABEL_CACHE:
        _LABEL_CACHE[norm] = translate_text(norm)
    return _LABEL_CACHE[norm]


def translate_value(text):
    norm = norm_de(text)
    if norm not in _VALUE_CACHE:
        _VALUE_CACHE[norm] = translate_text(norm, TRANS_VALUE)
    return _VALUE_CACHE[norm]


def esc(text):
    """HTML-escape a possibly-None string, with whitespace normalized."""
    if text is None:
        return ""
    return html.escape(str(text).strip())


def find_xml_in_zip(zip_path):
    """Return (member_name, xml_bytes) for the analysis log inside the zip.

    Prefers members named FAP_*.xml; otherwise takes the largest .xml member.
    Raises ValueError if the zip contains no .xml member.
    """
    with zipfile.ZipFile(zip_path) as zf:
        xml_members = [i for i in zf.infolist()
                       if i.filename.lower().endswith(".xml")]
        if not xml_members:
            raise ValueError("No .xml member found in zipfile: %s" % zip_path)
        fap = [i for i in xml_members
               if os.path.basename(i.filename).upper().startswith("FAP_")]
        pick = fap[0] if fap else max(xml_members, key=lambda i: i.file_size)
        return pick.filename, zf.read(pick)


def load_log(input_path):
    """Load the VAL XML from a .zip archive or directly from a .xml file.

    Returns (source_description, ElementTree root).
    """
    lower = input_path.lower()
    if lower.endswith(".xml"):
        with open(input_path, "rb") as f:
            data = f.read()
        source = os.path.basename(input_path)
    else:
        member, data = find_xml_in_zip(input_path)
        source = "%s :: %s" % (os.path.basename(input_path), member)
    root = ET.fromstring(data)
    if root.tag != "RESULTS":
        raise ValueError("Unexpected XML root element <%s>; expected "
                         "<RESULTS> (PIWIS vehicle analysis log)" % root.tag)
    return source, root


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

CSS = """
:root { --ink:#1a1a1a; --dim:#666; --line:#d8d8d8; --paper:#f5f6f7;
        --card:#ffffff; --accent:#005a9c; --fault:#b00020; --faultbg:#fdecea;
        --okbg:#eef5ee; }
* { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--ink);
       font:14px/1.45 "Segoe UI",Arial,Helvetica,sans-serif; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
.topbar { position:sticky; top:0; z-index:10; background:var(--card);
          border-bottom:1px solid var(--line); padding:8px 16px;
          display:flex; flex-wrap:wrap; align-items:center; gap:10px;
          box-shadow:0 1px 4px rgba(0,0,0,0.06); }
.topbar h1 { font-size:16px; margin:0 12px 0 0; white-space:nowrap; }
.topbar .vin { color:var(--dim); font-size:13px; margin-right:auto; }
.topbar input { padding:5px 9px; border:1px solid var(--line);
                border-radius:5px; width:230px; font-size:13px; }
.topbar button { padding:5px 10px; border:1px solid var(--line);
                 border-radius:5px; background:#fafafa; cursor:pointer;
                 font-size:13px; }
.topbar button:hover { background:#f0f0f0; }
#filtercount { color:var(--dim); font-size:12px; min-width:110px; }
.navlinks { font-size:13px; display:flex; gap:10px; }
.wrap { max-width:1100px; margin:0 auto; padding:16px; }
.cards { display:flex; flex-wrap:wrap; gap:16px; }
.card { background:var(--card); border:1px solid var(--line);
        border-radius:8px; padding:12px 16px; flex:1 1 380px; }
.card h2 { font-size:15px; margin:0 0 8px 0; }
table.kv { width:100%; border-collapse:collapse; }
table.kv td { padding:3px 8px 3px 0; vertical-align:top; }
table.kv td.lbl { color:var(--dim); width:45%; }
h2.sect { font-size:17px; margin:26px 0 10px 0; }
table.grid { width:100%; border-collapse:collapse; background:var(--card);
             border:1px solid var(--line); }
table.grid th { background:#ececec; text-align:left; padding:6px 8px;
                border:1px solid var(--line); font-size:13px; }
table.grid td { padding:5px 8px; border:1px solid var(--line);
                vertical-align:top; overflow-wrap:anywhere; }
table.grid tr:nth-child(even) td { background:#fafafa; }
tr.hasfault td { background:var(--faultbg) !important; }
details.ecu { background:var(--card); border:1px solid var(--line);
              border-radius:8px; margin:10px 0; }
details.ecu > summary { cursor:pointer; padding:9px 14px; font-weight:600;
                        display:flex; align-items:center; gap:10px;
                        list-style-position:inside; }
details.ecu > summary:hover { background:#f7f7f7; }
details.ecu[open] > summary { border-bottom:1px solid var(--line); }
details.ecu .body { padding:6px 14px 12px 14px; }
.badge { display:inline-block; border-radius:10px; padding:1px 9px;
         font-size:12px; font-weight:600; }
.badge.fault { background:var(--fault); color:#fff; }
.badge.count { background:#e4e9ee; color:#333; font-weight:normal; }
h3.meas { font-size:13.5px; margin:14px 0 4px 0; color:var(--accent);
          text-transform:none; }
table.vals { width:100%; border-collapse:collapse; }
table.vals td { padding:3px 8px; border-bottom:1px solid #eee;
                vertical-align:top; overflow-wrap:anywhere; }
table.vals td.lbl { color:#444; width:55%; }
table.vals td.val { font-family:Consolas,monospace; font-size:13px; }
.faultitem { background:var(--faultbg); border:1px solid #e6b3ac;
             border-radius:6px; padding:8px 12px; margin:8px 0; }
.faultitem .fcode { font-family:Consolas,monospace; font-weight:700;
                    color:var(--fault); margin-right:8px; }
a.fl { text-decoration:none; }
a.fl:hover { text-decoration:none; filter:brightness(1.15); }
.fltecu { margin:8px 0 16px 0; }
.subtable { margin:6px 0 0 22px; }
.footer { color:var(--dim); font-size:12px; margin:30px 0 14px 0;
          text-align:center; }
.gloss { color:var(--dim); font-weight:normal; font-size:12px; }
"""

JS = """
var debounce = null;
function onFilterInput() {
  if (debounce) clearTimeout(debounce);
  debounce = setTimeout(applyFilter, 150);
}
function applyFilter() {
  var q = document.getElementById('filterbox').value.trim().toLowerCase();
  // Filterable units: value rows and fault banners. A fault banner's
  // textContent includes its nested tables, so it stays visible whenever
  // any of its detail rows match.
  var units = document.querySelectorAll('tr.frow, div.faultitem');
  var shown = 0;
  for (var i = 0; i < units.length; i++) {
    var hit = !q || units[i].textContent.toLowerCase().indexOf(q) >= 0;
    units[i].style.display = hit ? '' : 'none';
    if (hit) shown++;
  }
  var secs = document.querySelectorAll('details.ecu');
  for (var j = 0; j < secs.length; j++) {
    var vis = 0;
    var srows = secs[j].querySelectorAll('tr.frow, div.faultitem');
    for (var k = 0; k < srows.length; k++)
      if (srows[k].style.display !== 'none') vis++;
    if (q) {
      secs[j].style.display = vis ? '' : 'none';
      if (vis) secs[j].open = true;
    } else {
      secs[j].style.display = '';
    }
  }
  document.getElementById('filtercount').textContent =
    q ? (shown + ' matching items') : '';
}
function setAll(open) {
  var secs = document.querySelectorAll('details.ecu');
  for (var i = 0; i < secs.length; i++) secs[i].open = open;
}
function presetFilter(q) {
  var b = document.getElementById('filterbox');
  b.value = q;
  applyFilter();
  b.focus();
  return false;
}
function openFor(el) {
  // open the element itself (if collapsible) and any collapsed ancestor
  if (!el) return;
  if (el.tagName === 'DETAILS') el.open = true;
  if (el.closest) {
    var p = el.closest('details');
    while (p) { p.open = true; p = p.parentElement &&
                p.parentElement.closest('details'); }
  }
}
function openTarget() {
  if (!location.hash) return;
  openFor(document.getElementById(location.hash.substring(1)));
}
window.addEventListener('hashchange', openTarget);
document.addEventListener('DOMContentLoaded', function () {
  openTarget();
  document.body.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a[href^="#"]') : null;
    if (!a) return;
    var id = a.getAttribute('href').substring(1);
    var t = document.getElementById(id);
    if (!t) return;
    if (a.closest('summary')) {
      // navigate without toggling the enclosing section
      e.preventDefault();
      openFor(t);
      if (location.hash === '#' + id) t.scrollIntoView();
      else location.hash = id;
    } else {
      openFor(t);
    }
  });
});
"""


def tr_html(orig, translated):
    """Escaped HTML for a possibly-translated string. When the translation
    changed something, the original text is kept as a hover tooltip."""
    orig = (orig or "").strip()
    if not orig:
        return ""
    if translated == norm_de(orig):
        return esc(orig)  # nothing translated: show the original as-is
    return '<span title="%s">%s</span>' % (esc(orig), esc(translated))


def label_html(orig):
    return tr_html(orig, translate_label(orig))


def value_html(orig):
    return tr_html(orig, translate_value(orig))


def unit_conversion(txt, unit):
    """Imperial equivalent for km and bar values, e.g. '(31054 mi)'.
    Returns '' when the unit or value does not qualify."""
    if unit == "km":
        factor, cunit = 0.621371, "mi"
    elif unit == "bar":
        factor, cunit = 14.5038, "psi"
    else:
        return ""
    try:
        val = float(txt.replace(",", "."))
    except ValueError:
        return ""
    conv = val * factor
    if "." in txt or "," in txt:
        s = "%.1f" % conv
    else:
        s = "%d" % round(conv)
    return " (%s&nbsp;%s)" % (s, cunit)


def value_cell(value_el):
    """Format a VALUE element's text plus its UNIT attribute."""
    txt = (value_el.text or "").strip()
    unit = (value_el.get("UNIT") or "").strip()
    body = value_html(txt)
    if unit:
        u = esc(TRANS_UNIT.get(norm_de(unit), unit))
        cell = "%s&nbsp;%s" % (body, u) if body else u
        return cell + unit_conversion(txt, unit)
    return body


def kv_row(label, value):
    return ('<tr><td class="lbl">%s</td><td>%s</td></tr>' % (label, value))


def header_cards(root):
    """The 'Vehicle data' and 'Tester' cards from RESULTSHEADER/HEADER."""
    rh = root.find("RESULTSHEADER")
    res = root.find("RESULT")
    hd = res.find("HEADER") if res is not None else None
    eq = hd.find("EQUIPMENT") if hd is not None else None

    def gt(parent, path):
        return esc(parent.findtext(path)) if parent is not None else ""

    def gtu(parent, path):
        # value plus UNIT attribute
        if parent is None:
            return ""
        el = parent.find(path)
        if el is None:
            return ""
        return value_cell(el)

    veh = rh.find("VEHICLE") if rh is not None else None
    proto = gt(hd, "PROTOKOLLTYPE")
    proto_disp = PROTOCOL_TYPES.get(proto, "Special-VAL (%s)" % proto
                                    if proto else "")

    vehicle_rows = [
        ("Creation date", gt(hd, "END_TEST")),
        ("Test started", gt(hd, "START_TEST")),
        ("Vehicle identification number", gt(veh, "IDENT/VIN")),
        ("Model line", gt(veh, "DATA/MODELTYPE")),
        ("Order type", gt(veh, "DATA/ORDERTYPE")),
        ("Mileage", gtu(veh, "DATA/ODOMETER")),
        ("Operating hours counter", gtu(veh, "DATA/OPERATINGTIME")),
        ("Transmission", gt(veh, "DATA/GEARBOXTYPE")),
        ("Engine type", gt(veh, "DATA/ENGINETYPE")),
        ("Country", gt(veh, "DATA/COUNTRYCODE")),
        ("Log type", proto_disp),
        ("Vehicle electrical system voltage", gtu(veh, "DATA/ONBOARDVOLTAGE")),
    ]
    tester_rows = [
        ("Dealer number", gt(rh, "CARDEALER/DEALERNO")),
        ("Tester ID", gt(eq, "SERIAL_NO")),
        ("Tester version", gt(eq, "VERSION")),
        ("PT3G version", gt(eq, "PT2GVERSION")),
        ("Model lines PDX", gt(eq, "BR_PDX")),
        ("VCI", gt(eq, "MODEL")),
        ("PDU API version", gt(eq, "PDU_API")),
        ("Operating system", gt(eq, "SYSTEM")),
        ("JAVA", gt(eq, "JAVA")),
        ("User mode", gt(eq, "MODE")),
        ("Time zone", gt(hd, "TIMEZONE")),
    ]
    out = ['<div class="cards">']
    for title, rows in (("Vehicle data", vehicle_rows),
                        ("Tester", tester_rows)):
        out.append('<div class="card"><h2>%s</h2><table class="kv">' % title)
        for lbl, val in rows:
            out.append(kv_row(lbl, val))
        out.append('</table></div>')
    out.append('</div>')
    return "\n".join(out)


def ident_value(section, label):
    """First Identifikation VALUE with the given LABEL attribute, or ''."""
    for meas in section.findall("MEAS[@OBJECT='Identifikation']"):
        for v in meas.findall("VALUE"):
            if v.get("LABEL") == label:
                return esc(v.text)
    return ""


def ecu_display(sec):
    """English-first link text for a control unit; the German designator
    is shown as a hover tooltip. Falls back to the original title when no
    English gloss is known (e.g. brand names)."""
    raw = (sec.findtext("TITLE") or "").strip()
    gloss = ECU_GLOSS.get(norm_de(raw))
    if gloss:
        return '<span title="%s">%s</span>' % (
            esc(raw), esc(gloss[0].upper() + gloss[1:]))
    return esc(raw)


def fault_values(section):
    """List of raw (code, description) for each fault VALUE in the section."""
    faults = []
    for meas in section.findall("MEAS[@OBJECT='Fehler']"):
        for v in meas.findall("VALUE"):
            faults.append(((v.text or "").strip(),
                           (v.get("TEXT") or "").strip()))
    return faults


def overview_table(sections, sec_ids):
    """Overview table: one row per ECU with key identity data and
    faults. The 'Overview' heading itself is emitted by build_html so
    the faults card can sit between the heading and this table."""
    out = ['<table class="grid"><tr>'
           '<th>Control unit</th><th>Part number</th><th>Serial number</th>'
           '<th>DSN</th><th>Software</th><th>Hardware</th>'
           '<th>Fault codes</th></tr>']
    for sec, sid in zip(sections, sec_ids):
        title = ecu_display(sec)
        sw = ident_value(sec, "PIF") or ident_value(sec, "ZIF")
        faults = fault_values(sec)
        fcell = ", ".join(
            '<span title="%s">%s</span>'
            % (esc(translate_label(desc)), esc(code))
            for code, desc in faults) or "&mdash;"
        cls = ' class="frow hasfault"' if faults else ' class="frow"'
        out.append(
            '<tr%s><td><a href="#%s">%s</a></td>'
            '<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
            '<td>%s</td></tr>' % (
                cls, sid, title,
                ident_value(sec, "SGIDK2"), ident_value(sec, "SERNR"),
                ident_value(sec, "SGIDK1"), sw, ident_value(sec, "BRIF"),
                fcell))
    out.append('</table>')
    return "\n".join(out)


def coding_table(sections, sec_ids):
    """Coding overview: one collapsible block per ECU with coding
    values, so the (very long) list can be scanned and filtered."""
    out = ['<h2 class="sect" id="coding">Coding overview</h2>']
    have_any = False
    for i, (sec, sid) in enumerate(zip(sections, sec_ids)):
        rows = []
        for meas in sec.findall("MEAS[@OBJECT='Codierung']"):
            for v in meas.findall("VALUE"):
                rows.append('<tr class="frow"><td class="lbl">%s</td>'
                            '<td class="val">%s</td></tr>'
                            % (label_html(v.get("TEXT")), value_cell(v)))
        if not rows:
            continue
        have_any = True
        out.append(
            '<details class="ecu" id="cod-%d">'
            '<summary>%s <span class="badge count">%d codings</span>'
            '<a style="margin-left:auto;font-weight:normal;font-size:12px"'
            ' href="#%s">control unit details</a></summary>'
            '<div class="body"><table class="vals">%s</table></div>'
            '</details>' % (i, ecu_display(sec), len(rows), sid,
                            "".join(rows)))
    if not have_any:
        return ""
    return "\n".join(out)


def render_meas_values(meas):
    """Generic label/value table for one MEAS block."""
    rows = []
    for v in meas.findall("VALUE"):
        rows.append('<tr class="frow"><td class="lbl">%s</td>'
                    '<td class="val">%s</td></tr>'
                    % (label_html(v.get("TEXT")), value_cell(v)))
    if not rows:
        return ""
    return '<table class="vals">%s</table>' % "".join(rows)


def meas_heading(meas):
    obj = meas.get("OBJECT") or ""
    title = MEAS_OBJECT_TITLES.get(obj, esc(meas.findtext("TITLE")) or
                                   esc(obj))
    native = esc(meas.findtext("TITLE"))
    if native and native.lower() != title.lower():
        return "%s <span style='color:#999;font-weight:normal'>(%s)</span>" \
            % (title, native)
    return title


def render_fault_meas(meas):
    """Fault memory: each fault code with its nested extended-memory table."""
    out = []
    nested = meas.findall("MEAS")
    for v in meas.findall("VALUE"):
        out.append('<div class="faultitem">')
        out.append('<span class="fcode">%s</span>%s'
                   % (esc(v.text), label_html(v.get("TEXT"))))
        for sub in nested:
            body = render_meas_values(sub)
            if body:
                out.append('<div class="subtable"><h3 class="meas">%s</h3>%s'
                           '</div>' % (meas_heading(sub), body))
        out.append('</div>')
    # nested tables with no parent fault VALUE (defensive)
    if not meas.findall("VALUE"):
        for sub in nested:
            body = render_meas_values(sub)
            if body:
                out.append('<div class="subtable"><h3 class="meas">%s</h3>%s'
                           '</div>' % (meas_heading(sub), body))
    return "\n".join(out)


def faults_section(sections, sec_ids, nfaults):
    """A faults-only view: just the fault codes, descriptions, and
    extended fault memory of every faulted control unit, without the
    rest of that unit's (long) tables. The red fault badges link here."""
    items = []
    for sec, sid in zip(sections, sec_ids):
        fmeas = [m for m in sec.findall("MEAS[@OBJECT='Fehler']")
                 if m.findall("VALUE") or m.findall("MEAS")]
        if not fmeas:
            continue
        body = "".join(render_fault_meas(m) for m in fmeas)
        items.append('<div class="fltecu" id="flt-%s">'
                     '<h3 class="meas"><a href="#%s">%s</a></h3>%s</div>'
                     % (sid, sid, ecu_display(sec), body))
    if not items:
        return ""
    return ('<details class="ecu" id="faults" open>'
            '<summary>Faults '
            '<span class="badge fault">%d fault%s</span></summary>'
            '<div class="body">%s</div></details>'
            % (nfaults, "" if nfaults == 1 else "s", "".join(items)))


def ecu_section(sec, sid):
    """One collapsible <details> block for a control unit. English name
    first; the German designator stays visible (small, gray) because it
    is what PIWIS itself displays."""
    raw = (sec.findtext("TITLE") or "").strip()
    gloss = ECU_GLOSS.get(norm_de(raw))
    if gloss:
        title = '%s <span class="gloss">%s</span>' % (
            esc(gloss[0].upper() + gloss[1:]), esc(raw))
    else:
        title = esc(raw)
    faults = fault_values(sec)
    nvals = len(sec.findall(".//VALUE"))
    badge = ('<a class="fl" href="#flt-%s" '
             'title="show only the fault entries">'
             '<span class="badge fault">%d fault%s</span></a>'
             % (sid, len(faults), "" if len(faults) == 1 else "s")) \
        if faults else ""
    open_attr = " open" if faults else ""
    out = ['<details class="ecu" id="%s"%s>' % (sid, open_attr),
           '<summary>%s %s<span class="badge count">%d values</span>'
           '<a style="margin-left:auto;font-weight:normal;font-size:12px"'
           ' href="#overview">back to top</a></summary>' %
           (title, badge, nvals),
           '<div class="body">']
    for meas in sec.findall("MEAS"):
        obj = meas.get("OBJECT") or ""
        if obj == "Fehler":
            body = render_fault_meas(meas)
            if body:
                out.append('<h3 class="meas">%s</h3>%s'
                           % (meas_heading(meas), body))
        else:
            body = render_meas_values(meas)
            if body:
                out.append('<h3 class="meas">%s</h3>%s'
                           % (meas_heading(meas), body))
    out.append('</div></details>')
    return "\n".join(out)


def build_html(root, source_desc):
    res = root.find("RESULT")
    title = esc(res.findtext("TITLE")) if res is not None \
        else "Vehicle analysis log"
    vin = esc(root.findtext("RESULTSHEADER/VEHICLE/IDENT/VIN"))
    created = esc(root.findtext("RESULT/HEADER/END_TEST"))
    sections = res.findall("SECTION[@OBJECT='ECU']") if res is not None \
        else []
    sec_ids = ["ecu-%d" % i for i in range(len(sections))]
    nfaults = sum(len(fault_values(s)) for s in sections)

    parts = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head><meta charset="utf-8">')
    parts.append("<title>%s - %s</title>" % (title, vin))
    parts.append("<style>%s</style>" % CSS)
    parts.append("<script>%s</script>" % JS)
    parts.append("</head><body>")
    nav = ('<a href="#overview">Overview</a>'
           + ('<a href="#faults">Faults</a>' if nfaults else '')
           + '<a href="#coding">Coding</a>'
             '<a href="#ecus">Control units</a>'
             '<a href="#" title="filter for Nmax over-rev ranges '
             '(supported vehicles only)" '
             'onclick="return presetFilter(\'Nmax\')">Overrevs</a>')
    parts.append(
        ('<div class="topbar"><h1>%s</h1>'
         '<span class="vin">%s &middot; %s</span>'
         '<div class="navlinks">%s</div>'
         '<input id="filterbox" type="search" '
         'placeholder="Filter rows (label, value, fault code)..." '
         'oninput="onFilterInput()">'
         '<span id="filtercount"></span>'
         '<button onclick="setAll(true)">Expand all</button>'
         '<button onclick="setAll(false)">Collapse all</button></div>')
        % (title, vin, created, nav))
    parts.append('<div class="wrap">')
    parts.append(header_cards(root))
    parts.append('<h2 class="sect" id="overview">Overview</h2>')
    if nfaults:
        parts.append(faults_section(sections, sec_ids, nfaults))
    parts.append(overview_table(sections, sec_ids))
    coding = coding_table(sections, sec_ids)
    if coding:
        parts.append(coding)
    parts.append('<h2 class="sect" id="ecus">Control units '
                 '<span class="badge count">%d ECUs</span></h2>'
                 % len(sections))
    for sec, sid in zip(sections, sec_ids):
        parts.append(ecu_section(sec, sid))
    parts.append('<div class="footer">Generated by %s from %s on %s<br>'
                 'German labels and values are translated to English '
                 'where known; hover a translated item to see the '
                 'original text.</div>'
                 % (APP_NAME, esc(source_desc),
                    time.strftime("%Y-%m-%d %H:%M:%S")))
    parts.append("</div></body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# UI / main
# ---------------------------------------------------------------------------

def pick_file_gui():
    """File-selection dialog; returns path or None if cancelled."""
    import tkinter
    from tkinter import filedialog
    tk = tkinter.Tk()
    tk.withdraw()
    tk.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Select a PIWIS vehicle analysis log",
        filetypes=[("PIWIS VAL zipfile", "*.zip"),
                   ("PIWIS VAL XML", "*.xml"),
                   ("All files", "*.*")])
    tk.destroy()
    return path or None


def show_error(message, gui):
    if gui:
        try:
            import tkinter
            from tkinter import messagebox
            tk = tkinter.Tk()
            tk.withdraw()
            messagebox.showerror(APP_NAME, message)
            tk.destroy()
            return
        except Exception:
            pass
    sys.stderr.write("ERROR: %s\n" % message)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="piwis_val_viewer",
        description="Render a PIWIS 3 vehicle analysis log (zip or xml) "
                    "to HTML and open it in the default browser.")
    ap.add_argument("input", nargs="?",
                    help="zipfile or xml file (omit for a file dialog)")
    ap.add_argument("--out", help="write HTML here instead of a temp file")
    ap.add_argument("--no-open", action="store_true",
                    help="do not launch the browser")
    args = ap.parse_args(argv)

    gui = args.input is None
    input_path = args.input
    if gui:
        input_path = pick_file_gui()
        if not input_path:
            return 0  # user cancelled

    if not os.path.isfile(input_path):
        show_error("File not found: %s" % input_path, gui)
        return 1

    try:
        source_desc, root = load_log(input_path)
    except (zipfile.BadZipFile, ValueError, ET.ParseError, OSError) as e:
        show_error("Could not read analysis log:\n%s" % e, gui)
        return 1

    try:
        html_text = build_html(root, source_desc)
    except Exception as e:
        show_error("Could not render analysis log:\n%s" % e, gui)
        return 1

    if args.out:
        out_path = args.out
    else:
        fd, out_path = tempfile.mkstemp(prefix="piwis_val_",
                                        suffix=".html")
        os.close(fd)
    # ASCII output: any non-ASCII text becomes a numeric character reference
    with open(out_path, "w", encoding="ascii",
              errors="xmlcharrefreplace", newline="\n") as f:
        f.write(html_text)

    if not args.no_open:
        webbrowser.open("file:///" + os.path.abspath(out_path)
                        .replace("\\", "/"))
    else:
        print("Wrote %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
