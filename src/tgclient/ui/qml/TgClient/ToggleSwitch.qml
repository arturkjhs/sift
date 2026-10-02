import QtQuick
import QtQuick.Controls.Basic

// On/off switch in the app's palette.
AbstractButton {
    id: root
    checkable: true
    implicitWidth: 38
    implicitHeight: 22
    opacity: enabled ? 1 : 0.5

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    background: Rectangle {
        radius: height / 2
        color: root.checked ? Theme.accent : Theme.badgeMuted

        Rectangle {
            width: parent.height - 4
            height: width
            radius: width / 2
            y: 2
            x: root.checked ? parent.width - width - 2 : 2
            color: "#FFFFFF"
            Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
        }
    }
    contentItem: Item {}
}
