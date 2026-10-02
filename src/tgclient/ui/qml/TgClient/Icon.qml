import QtQuick
import QtQuick.Window

// One of the app's own line icons (ui/icons.py), tinted with any color.
Image {
    id: root
    property string name: ""
    property color color: Theme.textMuted
    property int size: 18

    width: size
    height: size
    visible: name !== ""
    source: name !== "" ? "image://icon/" + name + "/" + color.toString().slice(-6) : ""
    sourceSize.width: Math.ceil(size * Screen.devicePixelRatio)
    sourceSize.height: Math.ceil(size * Screen.devicePixelRatio)
    smooth: true
}
