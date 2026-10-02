pragma Singleton
import QtQuick

// Design tokens. Quiet, cool neutrals for long daily use; one pine-teal accent reserved for
// things that need attention (unread counters, active folder, primary actions).
QtObject {
    readonly property string mode: shell ? shell.theme : "system"   // shell is gone at teardown
    readonly property bool dark: mode === "dark"
                                 || (mode !== "light" && Application.styleHints.colorScheme === Qt.Dark)

    readonly property color window: dark ? "#14181D" : "#EEF1F4"
    readonly property color sidebar: dark ? "#1B2027" : "#FFFFFF"
    readonly property color text: dark ? "#E4E8ED" : "#18202B"
    readonly property color textMuted: dark ? "#8A94A1" : "#6A7483"
    readonly property color separator: dark ? "#2A3038" : "#E1E5EA"
    readonly property color hover: dark ? "#222831" : "#F3F5F8"
    readonly property color selection: dark ? "#1F3A33" : "#DDEFEA"
    readonly property color accent: dark ? "#3FB295" : "#0E7C66"
    readonly property color textOnAccent: "#FFFFFF"
    readonly property color badgeMuted: dark ? "#4E5764" : "#A3ABB6"
    readonly property color danger: dark ? "#F07A6E" : "#C2412F"
    readonly property color field: dark ? "#232932" : "#FFFFFF"
    readonly property color fieldBorder: dark ? "#353C46" : "#CDD3DA"
    // Menus and popups float above everything: a touch lighter than the sidebar in dark mode.
    readonly property color popup: dark ? "#232932" : "#FFFFFF"
    readonly property color popupBorder: dark ? "#323A45" : "#E1E5EA"
    readonly property color shadow: dark ? "#66000000" : "#22000000"
    readonly property color flash: dark ? "#403FB295" : "#330E7C66"   // jumped-to message

    readonly property color bubbleIn: dark ? "#232A33" : "#FFFFFF"
    readonly property color bubbleOut: dark ? "#1D4239" : "#D8EFE7"
    readonly property color pill: dark ? "#232A33" : "#DFE4EA"
    readonly property color replyBar: accent
    readonly property color replyBackground: dark ? "#2A3540" : "#EEF3F1"
    readonly property color link: dark ? "#5FC9AE" : "#0B6E5A"
    readonly property color codeBackground: dark ? "#2C343E" : "#E8ECF0"
    readonly property color spoiler: dark ? "#55606D" : "#9AA3AE"

    readonly property var avatarColors: ["#D4695B", "#D9913B", "#8E6FD1", "#3E9C6B",
                                         "#3A8DBF", "#C25A93", "#6C7F99"]

    readonly property int fontTitle: 14
    readonly property int fontBody: 13
    readonly property int fontSmall: 12
}
