const API_URL = "http://localhost:5050/post";

function extractPlayerData(arr, start) {
  return {
    name: arr[start],
    id: arr[start + 1],
    bmRconUrl: arr[start + 2],
  };
}

function sendResponse(e) {
  const itemResponses = e.response.getItemResponses();
  const token = itemResponses[0];
  const players = [extractPlayerData(itemResponses, 1)];
  const reasons = itemResponses[4].split(";");
  const description = itemResponses[5];
  const attachmentUrls = itemResponses[6].split(";");
  for (const i = 7; i < 7 + 4 * 4; i += 4) {
    if (!itemResponses[i]) break;
    players.push(extractPlayerData(itemResponses, i + 1));
  }

  const data = {
    id: e.response.getId(),
    timestamp: e.response.getTimestamp(),
    data: {
      token: token,
      players: players,
      reasons: reasons,
      body: description,
      attachmentUrls: attachmentUrls
    }
  };

  const method = itemResponses[23] ? "put" : "post"

  const options = {
    method,
    payload: JSON.stringify(data),
    contentType: "application/json; charset=utf-8",
  };

  UrlFetchApp.fetch(API_URL, options);
};