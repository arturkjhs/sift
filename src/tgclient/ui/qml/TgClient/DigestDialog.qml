import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Across chats: the digest (chats with the digest flag, since the previous digest) and
// "what did I promise" (chats with AI on). Links open the chat at the message.
Popup {
    id: root
    objectName: "digestDialog"

    signal messageRequested(var chatId, var messageId)

    readonly property bool pending: ai.globalState === "pending"
    readonly property bool isDigest: ai.globalSubject === "digest"

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(560, parent.width - 48)
    height: Math.min(620, parent.height - 48)
    modal: true
    padding: 18

    background: Rectangle {
        radius: 12
        color: Theme.sidebar
        border.width: 1
        border.color: Theme.separator
    }

    contentItem: ColumnLayout {
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            PillButton {
                text: qsTr("Digest")
                iconName: "inbox"
                filled: root.isDigest
                onClicked: ai.showGlobal("digest")
            }
            PillButton {
                text: qsTr("What did I promise")
                iconName: "person"
                filled: !root.isDigest
                onClicked: ai.showGlobal("promises")
            }
            Item { Layout.fillWidth: true }
            IconButton {
                iconName: "close"
                glyphSize: 12
                Accessible.name: qsTr("Close")
                onClicked: root.close()
            }
        }

        Text {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            text: root.isDigest
                  ? (ai.digestChats === 0
                     ? qsTr("No chats in the digest yet. Add them with “Include in the "
                            + "digest” in a chat's Summarize menu (AI must be on there).")
                     : ai.digestChats === 1
                       ? qsTr("What matters in 1 chat since the previous digest.")
                       : qsTr("What matters in %1 chats since the previous digest.")
                         .arg(ai.digestChats))
                  : qsTr("Things you said you would do, from the last 14 days of chats with "
                         + "AI on.")
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            PillButton {
                objectName: "globalRun"
                text: root.pending ? qsTr("Working…")
                      : root.isDigest ? qsTr("Make a digest") : qsTr("Find my promises")
                iconName: "sparkle"
                filled: true
                enabled: !root.pending && ai.configured
                         && (!root.isDigest || ai.digestChats > 0)
                onClicked: root.isDigest ? ai.digest() : ai.promises()
            }
            Text {
                Layout.fillWidth: true
                text: ai.globalInfo
                elide: Text.ElideRight
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
            }
        }

        Text {
            Layout.fillWidth: true
            visible: ai.globalState === "error"
            text: ai.globalError
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Theme.danger
            font.pixelSize: Theme.fontBody
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: Theme.separator
        }

        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: resultText.height
            boundsBehavior: Flickable.StopAtBounds
            opacity: root.pending ? 0.5 : 1
            ScrollBar.vertical: ScrollBar {}

            Text {
                id: resultText
                width: flick.width - 8
                text: ai.globalHtml
                textFormat: Text.RichText
                wrapMode: Text.Wrap
                color: Theme.text
                font.pixelSize: Theme.fontBody
                onLinkActivated: link => {
                    const target = ai.parseLink(link)
                    if (target[1] !== 0) {
                        root.close()
                        root.messageRequested(target[0], target[1])
                    } else {
                        Qt.openUrlExternally(link)
                    }
                }
                HoverHandler {
                    cursorShape: resultText.linkAt(point.position.x, point.position.y) !== ""
                                 ? Qt.PointingHandCursor : Qt.ArrowCursor
                }
            }
        }

        Text {
            Layout.fillWidth: true
            text: qsTr("Made by %1 via OpenRouter. Can be wrong: check the linked messages.")
                  .arg(ai.summaryModel)
            wrapMode: Text.Wrap
            color: Theme.textMuted
            font.pixelSize: 11
        }
    }
}
