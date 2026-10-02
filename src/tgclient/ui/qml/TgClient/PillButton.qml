import QtQuick
import QtQuick.Controls.Basic

// Rounded text button in the app's palette. `filled` marks the primary or active choice.
AbstractButton {
    id: root
    property bool filled: false
    property string iconName: ""

    implicitWidth: content.implicitWidth + 24
    implicitHeight: 28
    hoverEnabled: true
    opacity: enabled ? 1 : 0.5

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    background: Rectangle {
        radius: height / 2
        color: root.filled ? Theme.accent
             : root.down ? Theme.selection
             : root.hovered ? Theme.hover : "transparent"
        border.width: root.filled ? 0 : 1
        border.color: Theme.fieldBorder
    }

    contentItem: Item {
        Row {
            id: content
            anchors.centerIn: parent
            spacing: 5

            Icon {
                anchors.verticalCenter: parent.verticalCenter
                name: root.iconName
                color: root.filled ? Theme.textOnAccent : Theme.accent
                size: 14
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: root.text
                color: root.filled ? Theme.textOnAccent : Theme.text
                font.pixelSize: Theme.fontSmall
                font.weight: Font.DemiBold
            }
        }
    }
}
