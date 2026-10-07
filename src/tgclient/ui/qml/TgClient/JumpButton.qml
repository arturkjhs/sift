import QtQuick
import QtQuick.Controls.Basic

// Round button over the feed (to the newest message, next mention, next reaction) with an
// optional counter on top. Right click: menuRequested (e.g. "mark all as read").
AbstractButton {
    id: root
    property string iconName: ""
    property string glyph: ""      // instead of an icon, e.g. "@"
    property int count: 0
    property string accessibleName: ""
    signal menuRequested()

    width: 38
    height: 38
    hoverEnabled: true
    Accessible.name: accessibleName
    ToolTip.visible: hovered
    ToolTip.delay: 600
    ToolTip.text: accessibleName

    TapHandler {
        acceptedButtons: Qt.RightButton
        onTapped: root.menuRequested()
    }

    background: Item {
        Rectangle {
            anchors.fill: parent
            anchors.topMargin: 2
            anchors.bottomMargin: -2
            radius: width / 2
            color: Theme.shadow
            opacity: 0.5
        }
        Rectangle {
            anchors.fill: parent
            radius: width / 2
            color: root.hovered ? Theme.hover : Theme.popup
            border.width: 1
            border.color: Theme.popupBorder
        }
    }
    contentItem: Item {
        Icon {
            anchors.centerIn: parent
            name: root.iconName
            size: 18
        }
        Text {
            anchors.centerIn: parent
            visible: root.glyph !== ""
            text: root.glyph
            color: Theme.textMuted
            font.pixelSize: 18
            font.weight: Font.DemiBold
        }
    }

    Rectangle {  // counter
        objectName: "jumpCount"
        visible: root.count > 0
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.top
        width: Math.max(height, countText.implicitWidth + 10)
        height: 18
        radius: height / 2
        color: Theme.accent

        Text {
            id: countText
            anchors.centerIn: parent
            text: root.count > 999 ? "999+" : root.count
            color: Theme.textOnAccent
            font.pixelSize: 11
            font.weight: Font.DemiBold
        }
    }
}
