import QtQuick
import QtQuick.Controls.Basic

ApplicationWindow {
    id: window
    width: 1100
    height: 720
    minimumWidth: 720
    minimumHeight: 480
    visible: true
    title: "tgclient"
    color: Theme.window

    onClosing: close => {
        close.accepted = false
        window.hide()
        shell.requestQuit()
    }

    Loader {
        anchors.fill: parent
        sourceComponent: auth.step === "ready" ? mainView : authView
    }

    Component {
        id: authView
        AuthView {}
    }

    Component {
        id: mainView
        MainView {
            onSettingsRequested: settingsDialog.open()
        }
    }

    SettingsDialog {
        id: settingsDialog
    }
}
