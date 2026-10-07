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

    // Click on a system notification: bring the window up and open that chat.
    Connections {
        target: accounts
        function onChatRequested(chatId) {
            window.show()
            window.raise()
            window.requestActivate()
            if (loader.item && loader.item.objectName === "mainView")
                loader.item.openChat(chatId, 0)
        }
    }

    Loader {
        id: loader
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

    MediaViewer {}

    // The passcode lock: over everything, popups included.
    LockScreen {
        parent: Overlay.overlay
        anchors.fill: parent
        z: 1000
        visible: lock.locked
    }

    Shortcut {
        sequence: "Ctrl+L"
        enabled: lock.hasPasscode && !lock.locked
        onActivated: lock.lockNow()
    }
}
