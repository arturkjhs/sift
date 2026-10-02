import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Summary of the open chat. Citations are tgc://message/<id> links that scroll the feed.
Rectangle {
    id: root
    objectName: "summaryPanel"
    color: Theme.sidebar

    signal closeRequested()
    signal messageRequested(var messageId)

    readonly property bool pending: ai.summaryState === "pending"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            spacing: 4

            Text {
                Layout.fillWidth: true
                text: ai.subject !== "" ? qsTr("About %1").arg(ai.summaryName) : qsTr("Summary")
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Theme.text
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
            }
            IconButton {
                iconName: "refresh"
                enabled: !root.pending && ai.enabled && ai.summaryScope !== ""
                Accessible.name: qsTr("Summarize again")
                onClicked: ai.summarizeAgain()
            }
            IconButton {
                iconName: "close"
                glyphSize: 12
                Accessible.name: qsTr("Close summary")
                onClicked: root.closeRequested()
            }
        }

        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: root.pending ? qsTr("Summarizing\u2026") : ai.summaryInfo
            color: root.pending ? Theme.accent : Theme.textMuted
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
        }

        Text {
            Layout.fillWidth: true
            visible: ai.summaryState === "error"
            text: ai.summaryError
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Theme.danger
            font.pixelSize: Theme.fontBody
        }

        Text {
            Layout.fillWidth: true
            visible: ai.summaryHtml === "" && ai.summaryState === ""
            text: qsTr("No summary yet.")
            color: Theme.textMuted
            font.pixelSize: Theme.fontBody
        }

        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: summaryText.height
            boundsBehavior: Flickable.StopAtBounds
            opacity: root.pending ? 0.5 : 1

            Text {
                id: summaryText
                width: flick.width - 8
                text: ai.summaryHtml
                textFormat: Text.RichText
                wrapMode: Text.Wrap
                color: Theme.text
                font.pixelSize: Theme.fontBody
                onLinkActivated: link => {
                    const id = ai.messageIdFromLink(link)
                    if (id !== 0)
                        root.messageRequested(id)
                    else
                        Qt.openUrlExternally(link)
                }

                HoverHandler {
                    cursorShape: summaryText.linkAt(point.position.x, point.position.y) !== ""
                                 ? Qt.PointingHandCursor : Qt.ArrowCursor
                }
            }

            ScrollBar.vertical: ScrollBar {}
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
