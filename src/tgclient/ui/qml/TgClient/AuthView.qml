import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window

Item {
    id: root
    objectName: "authView"

    readonly property var titles: ({
        loading: qsTr("Connecting"),
        phone: qsTr("Sign in"),
        code: qsTr("Enter the code"),
        password: qsTr("Enter your password"),
        email: qsTr("Add an email"),
        emailCode: qsTr("Check your email"),
        qr: qsTr("Scan to log in"),
        failed: qsTr("Sign-in stopped")
    })
    readonly property var placeholders: ({
        phone: "+420 123 456 789",
        code: qsTr("Code"),
        password: qsTr("Password"),
        email: "you@example.com",
        emailCode: qsTr("Code")
    })
    readonly property bool asksInput: placeholders[auth.step] !== undefined
    property string shownStep: ""

    function submit() {
        if (submitButton.enabled)
            auth.submit(input.text)
    }

    Connections {
        target: auth
        function onChanged() {
            if (auth.step !== root.shownStep) {
                root.shownStep = auth.step
                input.text = ""
                input.forceActiveFocus()
            }
        }
    }

    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(root.width - 48, 340)
        spacing: 12

        Label {
            Layout.fillWidth: true
            text: root.titles[auth.step] || ""
            color: Theme.text
            font.pixelSize: 22
            font.weight: Font.DemiBold
            wrapMode: Text.Wrap
        }

        Label {
            Layout.fillWidth: true
            visible: text.length > 0
            text: auth.hint
            color: Theme.textMuted
            font.pixelSize: Theme.fontTitle
            wrapMode: Text.Wrap
        }

        // QR login: the phone (Settings → Devices → Link Desktop Device) scans this code.
        ColumnLayout {
            Layout.fillWidth: true
            visible: auth.step === "qr"
            spacing: 12

            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                width: 232
                height: 232
                radius: 14
                color: "#FFFFFF"
                border.width: 1
                border.color: Theme.separator

                Image {
                    objectName: "qrImage"
                    anchors.centerIn: parent
                    width: 216
                    height: 216
                    source: auth.qrSource
                    sourceSize.width: Math.ceil(216 * Screen.devicePixelRatio)
                    sourceSize.height: Math.ceil(216 * Screen.devicePixelRatio)
                    smooth: false
                    cache: false
                }
            }
            Repeater {
                model: [qsTr("Open Telegram on your phone"),
                        qsTr("Go to Settings \u2192 Devices \u2192 Link Desktop Device"),
                        qsTr("Point your phone at this screen to confirm the login")]
                RowLayout {
                    required property string modelData
                    required property int index
                    Layout.fillWidth: true
                    spacing: 10
                    Rectangle {
                        Layout.alignment: Qt.AlignTop
                        width: 22
                        height: 22
                        radius: 11
                        color: Theme.accent
                        Text {
                            anchors.centerIn: parent
                            text: index + 1
                            color: Theme.textOnAccent
                            font.pixelSize: 12
                            font.weight: Font.Bold
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: modelData
                        wrapMode: Text.Wrap
                        color: Theme.text
                        font.pixelSize: Theme.fontTitle
                    }
                }
            }
        }

        TextField {
            id: input
            Layout.fillWidth: true
            visible: root.asksInput
            enabled: !auth.busy
            focus: true
            placeholderText: root.placeholders[auth.step] || ""
            echoMode: auth.step === "password" ? TextInput.Password : TextInput.Normal
            inputMethodHints: auth.step === "phone" ? Qt.ImhDialableCharactersOnly
                            : auth.step === "code" || auth.step === "emailCode" ? Qt.ImhDigitsOnly
                            : auth.step === "email" ? Qt.ImhEmailCharactersOnly
                            : Qt.ImhNone
            color: Theme.text
            placeholderTextColor: Theme.textMuted
            font.pixelSize: 15
            padding: 10
            background: Rectangle {
                radius: 8
                color: Theme.field
                border.width: input.activeFocus ? 2 : 1
                border.color: input.activeFocus ? Theme.accent : Theme.fieldBorder
            }
            onAccepted: root.submit()
        }

        Label {
            Layout.fillWidth: true
            visible: text.length > 0
            text: auth.error
            color: Theme.danger
            font.pixelSize: Theme.fontBody
            wrapMode: Text.Wrap
        }

        Button {
            id: submitButton
            Layout.fillWidth: true
            visible: root.asksInput
            enabled: !auth.busy && input.text.trim().length > 0
            text: auth.busy ? qsTr("Checking") : qsTr("Continue")
            padding: 10
            contentItem: Text {
                text: submitButton.text
                color: Theme.textOnAccent
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
            }
            background: Rectangle {
                radius: 8
                color: Theme.accent
                opacity: submitButton.enabled ? (submitButton.down ? 0.85 : 1) : 0.4
            }
            onClicked: root.submit()
        }

        // Other ways in, and leaving an account that's being added.
        Flow {
            Layout.alignment: Qt.AlignHCenter
            spacing: 18
            visible: !auth.busy

            Text {
                objectName: "qrLoginLink"
                visible: auth.step === "phone"
                text: qsTr("Log in by QR code")
                color: Theme.link
                font.pixelSize: Theme.fontBody
                font.weight: Font.DemiBold
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: auth.requestQr() }
            }
            Text {
                visible: auth.step === "qr" || auth.step === "failed"
                text: qsTr("Log in by phone number")
                color: Theme.link
                font.pixelSize: Theme.fontBody
                font.weight: Font.DemiBold
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: accounts.restartLogin() }
            }
            Text {
                visible: accounts.adding
                text: qsTr("Cancel")
                color: Theme.textMuted
                font.pixelSize: Theme.fontBody
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: accounts.cancelAdd() }
            }
        }

        BusyIndicator {
            Layout.alignment: Qt.AlignHCenter
            visible: running
            running: auth.step === "loading" || auth.busy
        }
    }
}
