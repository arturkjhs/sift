import QtQuick
import QtQuick.Controls.Basic

// At launch with a passcode: only this, until the database keys are unlocked.
ApplicationWindow {
    width: 480
    height: 520
    visible: true
    title: "tgclient"
    color: Theme.window

    onClosing: shell.requestQuit()

    LockScreen {
        anchors.fill: parent
    }
}
