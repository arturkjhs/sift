import QtQuick
import QtQuick.Controls.Basic

// A hover hint in the app's style (like AppMenu: rounded card, soft shadow, theme colours).
// Declared as a child of the item it explains; Basic's attached ToolTip is a plain grey box.
ToolTip {
    id: root
    delay: 600
    padding: 0
    margins: 6
    font.pixelSize: Theme.fontSmall

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 110 }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; to: 0; duration: 80 }
    }

    contentItem: Text {
        leftPadding: 10
        rightPadding: 10
        topPadding: 6
        bottomPadding: 6
        text: root.text
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Theme.text
        font: root.font
    }

    background: Item {
        implicitWidth: 40
        Repeater {  // shadow, no shader effects (software rendering)
            model: 2
            Rectangle {
                required property int index
                anchors.fill: parent
                anchors.margins: -index - 1
                anchors.bottomMargin: -index * 2 - 2
                radius: 9 + index
                color: Theme.shadow
                opacity: 0.3 - index * 0.1
            }
        }
        Rectangle {
            anchors.fill: parent
            radius: 8
            color: Theme.popup
            border.width: 1
            border.color: Theme.popupBorder
        }
    }
}
