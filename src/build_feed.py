from feedgen.feed import FeedGenerator

def build_podcast_feed(site_base_url, show_title, show_description, show_author, show_email,
                       episodes, out_feed_path):
    fg = FeedGenerator()
    try:
        fg.load_extension('podcast')
    except Exception:
        pass

    fg.title(show_title)
    fg.author({'name': show_author, 'email': show_email})
    fg.link(href=site_base_url, rel='alternate')
    fg.subtitle(show_description)
    fg.id(f"{site_base_url}/feed.xml")
    fg.link(href=f"{site_base_url}/feed.xml", rel='self')
    fg.language('en')

    for ep in episodes:
        fe = fg.add_entry()
        fe.id(ep['url'])
        fe.title(ep['title'])
        fe.link(href=ep['page_url'])
        fe.description(ep.get('summary', ''))
        fe.pubDate(ep['date'])
        fe.enclosure(ep['url'], str(ep['length']), 'audio/mpeg')

    fg.rss_file(out_feed_path)