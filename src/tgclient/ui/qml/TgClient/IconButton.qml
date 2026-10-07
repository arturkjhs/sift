import QtQuick
import QtQuick.Controls.Basic

// Flat square button with an icon (or a text glyph); background only on hover/press.
AbstractButton {
    id: root
    property string iconName: ""
    property int glyphSize: 15

    implicitWidth: 30
    implicitHeight: 30
    hoverEnabled: true
    opacity: enabled ? 1 : 0.4

    AppToolTip {
        visible: parent.hovered && parent.Accessible.name !== ""
        text: parent.Accessible.name
        delay: 600
    }

    background: Rectangle {
        radius: 6
        color: root.down ? Theme.selection : root.hovered ? Theme.hover : "transparent"
    }

    contentItem: Item {
        Icon {
            anchors.centerIn: parent
            name: root.iconName
            size: root.glyphSize + 3
        }
        Text {
            anchors.centerIn: parent
            visible: root.iconName === ""
            text: root.text
            color: Theme.textMuted
            font.pixelSize: root.glyphSize
        }
    }
}
