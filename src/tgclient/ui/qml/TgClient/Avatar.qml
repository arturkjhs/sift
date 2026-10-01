import QtQuick
import QtQuick.Window

// Round chat avatar: downloaded photo when available, otherwise initials on a stable color.
Item {
    id: root
    property int size: 44
    property string avatarSource: ""
    property string initials: ""
    property int colorIndex: 0

    width: size
    height: size

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        visible: photo.status !== Image.Ready
        color: Theme.avatarColors[root.colorIndex % Theme.avatarColors.length]

        Text {
            anchors.centerIn: parent
            text: root.initials
            color: "#FFFFFF"
            font.pixelSize: Math.round(root.size * 0.38)
            font.weight: Font.DemiBold
        }
    }

    Image {
        id: photo
        anchors.fill: parent
        visible: status === Image.Ready
        source: root.avatarSource
        sourceSize.width: Math.ceil(root.size * Screen.devicePixelRatio)
        sourceSize.height: Math.ceil(root.size * Screen.devicePixelRatio)
        asynchronous: true
        smooth: true
    }
}
