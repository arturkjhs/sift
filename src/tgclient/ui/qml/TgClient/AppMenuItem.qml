import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Menu row: icon, label, optional hint on the right (or under the label when `hintBelow`).
MenuItem {
    id: root
    property string iconName: ""
    property bool danger: false
    property string hint: ""

    readonly property color tint: !enabled ? Theme.textMuted : danger ? Theme.danger : Theme.text

    implicitWidth: Math.max(210, row.implicitWidth + leftPadding + rightPadding)
    implicitHeight: visible ? Math.max(34, row.implicitHeight + 12) : 0
    leftPadding: 10
    rightPadding: 14
    hoverEnabled: true
    indicator: null
    arrow: null

    HoverHandler { cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }

    background: Rectangle {
        radius: 6
        color: root.highlighted && root.enabled ? Theme.hover : "transparent"
    }

    contentItem: RowLayout {
        id: row
        spacing: 10

        Icon {
            Layout.alignment: Qt.AlignVCenter
            name: root.iconName
            color: root.danger ? Theme.danger : Theme.textMuted
            opacity: root.enabled ? 1 : 0.5
            size: 17
        }
        Item {  // keeps labels aligned in menus where some rows have no icon
            visible: root.iconName === ""
            implicitWidth: 17
        }
        Text {
            Layout.fillWidth: true
            text: root.text
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: root.tint
            opacity: root.enabled ? 1 : 0.6
            font.pixelSize: Theme.fontBody
        }
        Text {
            visible: root.hint !== ""
            text: root.hint
            color: Theme.textMuted
            font.pixelSize: 11
        }
    }
}
