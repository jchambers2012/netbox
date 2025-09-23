# GitHub JWT Backend

To use the GutHub JWT authentication type, you need to set up a GitHub App that allows for your connection to come from a personal account (much like a personal access token) or from an organization account you own or have access to.  One of the major benefits of using a GitHub App is:

> GitHub Apps are not tied to a user account and do not consume a seat. GitHub Apps remain installed even when the person who initially installed the app leaves the organization. This lets your integration continue to work even if people leave your team. [^1]

To register and install a new GitHub App, please refer to these KBs and save off the App ID, JWT Private PEM and Installation ID for use within NetBox:
- https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app
- https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app 

When asking for permissions, the application will need at lease read access to “Contents” to be able to list and pull files in from the repo.

Using the information above; navigate to Operations > Data Source and click Add.  Select the type to be “GitHub (JWT)” add fill out the JWT Private Key, App ID, and in the Access Token use one of the following URLs that will include the Installation ID:
- For GitHub Cloud / EMU: https://api.github.com/app/installations/{INSTALLATION ID HERE}/access_tokens
- For GitHub Enterprise Server v3.X: https://{DOMAIN HERE}/api/v3/app/installations/{INSTALLATION ID HERE}/access_tokens

[^1]: https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/deciding-when-to-build-a-github-app#github-apps-can-act-independently-of-or-on-behalf-of-a-user
