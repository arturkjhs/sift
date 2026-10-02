import QtQuick
import QtQuick.Controls.Basic

// Context menu in the app's style: rounded floating card with a soft shadow and a quick
// fade/scale-in. Use AppMenuItem and AppMenuSeparator inside.
Menu {
    id: root
    padding: 5
    margins: 8
    overlap: 0
    delegate: AppMenuItem {}

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 110 }
        NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 140; easing.type: Easing.OutCubic }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; to: 0; duration: 80 }
    }

    background: Item {
        implicitWidth: 220

        // Shadow: a few soft offset layers (no shader effects: works in software rendering).
        Repeater {
            model: 3
            Rectangle {
                required property int index
                anchors.fill: parent
                anchors.margins: -index - 1
                anchors.topMargin: -index
                anchors.bottomMargin: -index * 2 - 3
                radius: 11 + index
                color: Theme.shadow
                opacity: 0.35 - index * 0.1
            }
        }

        Rectangle {
            anchors.fill: parent
            radius: 10
            color: Theme.popup
            border.width: 1
            border.color: Theme.popupBorder
        }
    }
}
