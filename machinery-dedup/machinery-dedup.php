<?php
/**
 * Plugin Name:       Machinery News Dedup
 * Description:       Lightweight dedup for the-packing-machine.com scraper. Hooks REST API post inserts to detect articles already imported by source URL. Also injects rel="canonical" pointing to the original source for SEO + copyright safety.
 * Version:           0.1.0
 * Requires at least: 5.9
 * Requires PHP:      7.0
 * Author:            the-packing-machine
 * License:           GPL-2.0-or-later
 *
 * This replaces the heavy wp-rss-curator plugin for the immediate MVP need.
 * It does ONE thing: prevent the scraper from creating duplicate posts when
 * the same source URL is pushed twice (e.g. across cron runs).
 *
 * Meta keys used (must match what machinery-news-scraper pushes):
 *   _machinery_source_url      — original article URL (used for dedup)
 *   _machinery_source_id       — scraper source slug
 *   _machinery_source_type     — rss / linkedin / tradeshow
 *   _machinery_external_id     — guid or url
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

define( 'MACHINERY_DEDUP_VERSION', '0.1.0' );

/**
 * Register the custom meta fields the scraper writes.
 *
 * WP REST API silently drops meta keys that aren't registered with
 * show_in_rest=true. Without this, the scraper's _machinery_source_url
 * never gets saved → dedup can't find it → duplicates are created.
 *
 * These are "protected" meta (underscore prefix = WP convention for internal
 * meta not shown in Custom Fields UI). We register them as single-value
 * strings.
 */
add_action( 'init', function () {
    $keys = array(
        '_machinery_source_url',
        '_machinery_source_id',
        '_machinery_source_type',
        '_machinery_external_id',
        '_machinery_ai_summary',
        '_machinery_ai_category',
        '_machinery_ai_importance',
        '_machinery_ai_tags',
        '_machinery_ai_buyer_angle',
    );
    foreach ( $keys as $key ) {
        register_post_meta( 'post', $key, array(
            'type'          => 'string',
            'single'        => true,
            'show_in_rest'  => true,
            'auth_callback' => function () {
                return current_user_can( 'edit_posts' );
            },
        ) );
    }
} );

/**
 * Intercept REST API POST /wp-json/wp/v2/posts BEFORE the post is inserted.
 * If a post already exists with the same _machinery_source_url, return 409.
 *
 * The scraper (machinery-news-scraper) treats 409 as PushStatus.DUPLICATE,
 * which is the correct non-error outcome for an idempotent scraper.
 */
add_filter( 'rest_pre_insert_post', function ( $prepared, $request ) {
    $source_url = machinery_dedup_extract_source_url( $request );
    if ( ! $source_url ) {
        return $prepared;  // Not from our scraper, allow normal insert.
    }

    $existing_id = machinery_dedup_find_existing( $source_url );
    if ( $existing_id ) {
        return new WP_Error(
            'machinery_duplicate',
            __( 'Article already imported.', 'machinery-dedup' ),
            array(
                'status'      => 409,
                'existing_id' => $existing_id,
                'source_url'  => $source_url,
            )
        );
    }

    return $prepared;
}, 10, 2 );

/**
 * Extract _machinery_source_url from the REST request's meta param.
 * Handles both array-of-arrays format (REST spec) and flat array.
 *
 * @param WP_REST_Request $request
 * @return string Empty string if not present.
 */
function machinery_dedup_extract_source_url( $request ) {
    $meta = $request->get_param( 'meta' );
    if ( empty( $meta ) || ! is_array( $meta ) ) {
        return '';
    }

    // REST API sends meta as array( array('key' => ..., 'value' => ...), ... )
    foreach ( $meta as $entry ) {
        if ( ! is_array( $entry ) ) {
            continue;
        }
        if ( '_machinery_source_url' === ( $entry['key'] ?? '' ) ) {
            return is_string( $entry['value'] ) ? $entry['value'] : '';
        }
    }

    // Fallback: flat associative array (less common but defensive)
    if ( isset( $meta['_machinery_source_url'] ) && is_string( $meta['_machinery_source_url'] ) ) {
        return $meta['_machinery_source_url'];
    }

    return '';
}

/**
 * Find an existing post with the same source URL meta.
 *
 * @param string $source_url
 * @return int|null Post ID if found, null otherwise.
 */
function machinery_dedup_find_existing( $source_url ) {
    if ( '' === $source_url ) {
        return null;
    }

    $cache_key = 'md_url_' . md5( $source_url );
    $cached    = wp_cache_get( $cache_key, 'machinery_dedup' );
    if ( false !== $cached && (int) $cached > 0 ) {
        return (int) $cached;
    }

    // meta_query handles single-meta-key lookups efficiently.
    $query = new WP_Query( array(
        'post_type'      => 'post',
        'post_status'    => array( 'publish', 'pending', 'draft', 'future', 'private' ),
        'posts_per_page' => 1,
        'fields'         => 'ids',
        'no_found_rows'  => true,
        'meta_query'     => array(
            array(
                'key'   => '_machinery_source_url',
                'value' => $source_url,
            ),
        ),
    ) );

    if ( ! empty( $query->posts ) ) {
        $id = (int) $query->posts[0];
        wp_cache_set( $cache_key, $id, 'machinery_dedup', 3600 );
        return $id;
    }

    return null;
}

/**
 * Inject rel="canonical" pointing to the original source URL on single posts.
 * Search engines honor this for syndicated content — they'll index the original,
 * not our re-publication. This is the copyright-safe way to aggregate news.
 *
 * We use priority 1 to fire BEFORE other canonical filters (e.g. Rank Math,
 * Yoast) so they can react. Many SEO plugins remove default WP canonical when
 * they take over — they typically respect canonicals set by other plugins at
 * higher priority.
 */
add_action( 'wp_head', function () {
    if ( ! is_singular( 'post' ) ) {
        return;
    }
    $source_url = get_post_meta( get_queried_object_id(), '_machinery_source_url', true );
    if ( ! $source_url || ! filter_var( $source_url, FILTER_VALIDATE_URL ) ) {
        return;
    }
    echo '<link rel="canonical" href="' . esc_url( $source_url ) . '" />' . "\n";
    echo '<meta name="machinery-source" content="' . esc_attr( $source_url ) . '" />' . "\n";
}, 1 );

/**
 * Add "Source" column to admin Posts list so editors can see at a glance
 * which articles came from the scraper (vs. written in-house).
 */
add_filter( 'manage_post_posts_columns', function ( $columns ) {
    $columns['machinery_source'] = __( 'Source', 'machinery-dedup' );
    return $columns;
} );

add_action( 'manage_post_posts_custom_column', function ( $column, $post_id ) {
    if ( 'machinery_source' !== $column ) {
        return;
    }
    $url = get_post_meta( $post_id, '_machinery_source_url', true );
    $sid = get_post_meta( $post_id, '_machinery_source_id', true );
    if ( $url ) {
        $host = wp_parse_url( $url, PHP_URL_HOST );
        printf(
            '<a href="%s" target="_blank" rel="noopener" title="%s">%s</a>',
            esc_url( $url ),
            esc_attr( $url ),
            esc_html( $sid ? $sid : ( $host ?: 'source' ) )
        );
    } else {
        echo '—';
    }
}, 10, 2 );

/**
 * Log dedup hits to the error log so we can monitor health.
 * (Optional; remove if error log gets noisy.)
 */
add_action( 'machinery_dedup_hit', function ( $source_url, $existing_id ) {
    if ( defined( 'WP_DEBUG_LOG' ) && WP_DEBUG_LOG ) {
        error_log( sprintf(
            '[machinery-dedup] DUPLICATE blocked: source_url=%s existing_post_id=%d',
            $source_url,
            $existing_id
        ) );
    }
}, 10, 2 );

// Fire the logging action when a duplicate is detected (do_action so other
// plugins/themes can react too).
add_filter( 'rest_pre_insert_post', function ( $prepared, $request ) {
    // This runs AFTER the first rest_pre_insert_post filter (priority 10 vs 10).
    // If $prepared is a WP_Error with code 'machinery_duplicate', log it.
    if ( is_wp_error( $prepared ) && 'machinery_duplicate' === $prepared->get_error_code() ) {
        $data = $prepared->get_error_data();
        if ( isset( $data['source_url'] ) && isset( $data['existing_id'] ) ) {
            do_action( 'machinery_dedup_hit', $data['source_url'], $data['existing_id'] );
        }
    }
    return $prepared;
}, 11, 2 );


/* ----------------------------------------------------------------------
 * CUSTOM RSS FEEDS — 3 tracks like AIHOT does
 *   /feed/selected.xml  — top stories (importance >= 4), last 50
 *   /feed/all.xml       — all imported items, last 100
 *   /feed/weekly.xml    — top 20 from the past 7 days
 * ---------------------------------------------------------------------- */

/**
 * Register rewrite rules for our 3 feeds.
 */
add_action( 'init', function () {
    add_rewrite_rule( '^feed/selected\.xml$', 'index.php?machinery_feed=selected', 'top' );
    add_rewrite_rule( '^feed/all\.xml$',      'index.php?machinery_feed=all',      'top' );
    add_rewrite_rule( '^feed/weekly\.xml$',   'index.php?machinery_feed=weekly',   'top' );
} );

/**
 * Register the query var.
 */
add_filter( 'query_vars', function ( $vars ) {
    $vars[] = 'machinery_feed';
    return $vars;
} );

/**
 * Feed templates — output RSS 2.0 with our custom fields.
 */
add_action( 'template_redirect', function () {
    $feed = get_query_var( 'machinery_feed' );
    if ( ! $feed ) {
        return;
    }

    $args = array(
        'post_type'      => 'post',
        'post_status'    => 'publish',
        'orderby'        => 'date',
        'order'          => 'DESC',
        'posts_per_page' => 100,
        'no_found_rows'  => true,
        'meta_key'       => '_machinery_source_url',
    );

    switch ( $feed ) {
        case 'selected':
            $args['posts_per_page'] = 50;
            $args['meta_query']     = array(
                array(
                    'key'   => '_machinery_ai_importance',
                    'value' => '4',
                    'compare' => '>=',
                    'type'    => 'NUMERIC',
                ),
            );
            $feed_title = 'Machinery News Digest — Top Stories';
            break;
        case 'weekly':
            $args['posts_per_page'] = 20;
            $args['date_query']     = array(
                array(
                    'column' => 'post_date',
                    'after'  => '7 days ago',
                ),
            );
            $args['meta_query']     = array(
                array(
                    'key'     => '_machinery_ai_importance',
                    'value'   => '3',
                    'compare' => '>=',
                    'type'    => 'NUMERIC',
                ),
            );
            $args['orderby']        = 'meta_value_num';
            $args['meta_key']       = '_machinery_ai_importance';
            $args['order']          = 'DESC';
            $feed_title = 'Machinery News Digest — This Week';
            break;
        case 'all':
        default:
            $feed_title = 'Machinery News Digest — All Items';
            break;
    }

    $query = new WP_Query( $args );

    header( 'Content-Type: application/rss+xml; charset=' . get_option( 'blog_charset' ), true );

    echo '<?xml version="1.0" encoding="' . esc_attr( get_option( 'blog_charset' ) ) . '"?>' . "\n";
    ?>
<rss version="2.0"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title><?php echo esc_html( $feed_title ); ?></title>
    <link><?php echo esc_url( home_url( '/' ) ); ?></link>
    <description>B2B packaging-machinery industry news — AI-curated daily from packaging, materials, robotics, and trade publications.</description>
    <language>en-US</language>
    <lastBuildDate><?php echo esc_html( mysql2date( 'D, d M Y H:i:s +0000', gmdate( 'Y-m-d H:i:s' ), false ) ); ?></lastBuildDate>
    <atom:link href="<?php echo esc_url( home_url( "/feed/{$feed}.xml" ) ); ?>" rel="self" type="application/rss+xml" />
<?php
    while ( $query->have_posts() ) :
        $query->the_post();
        $post_id       = get_the_ID();
        $source_url    = get_post_meta( $post_id, '_machinery_source_url', true );
        $source_id     = get_post_meta( $post_id, '_machinery_source_id', true );
        $ai_summary    = get_post_meta( $post_id, '_machinery_ai_summary', true );
        $ai_category   = get_post_meta( $post_id, '_machinery_ai_category', true );
        $importance    = get_post_meta( $post_id, '_machinery_ai_importance', true );
        $ai_tags       = get_post_meta( $post_id, '_machinery_ai_tags', true );
        $buyer_angle   = get_post_meta( $post_id, '_machinery_ai_buyer_angle', true );
        $excerpt       = $ai_summary ? $ai_summary : get_the_excerpt();
        ?>
    <item>
      <title><?php echo esc_html( get_the_title() ); ?></title>
      <link><?php echo esc_url( get_permalink() ); ?></link>
      <guid isPermaLink="true"><?php echo esc_url( get_permalink() ); ?></guid>
      <pubDate><?php echo esc_html( mysql2date( 'D, d M Y H:i:s +0000', get_post_time( 'Y-m-d H:i:s', true ), false ) ); ?></pubDate>
      <dc:creator><?php echo esc_html( $source_id ? $source_id : 'machinery-news-digest' ); ?></dc:creator>
      <description><![CDATA[<?php echo wp_kses_post( $excerpt ); ?>]]></description>
      <?php if ( $buyer_angle ) : ?>
      <content:encoded><![CDATA[<p><strong>AI Summary:</strong> <?php echo esc_html( $ai_summary ); ?></p><p><strong>What this means for you:</strong> <?php echo esc_html( $buyer_angle ); ?></p>]]></content:encoded>
      <?php else : ?>
      <content:encoded><![CDATA[<?php echo wp_kses_post( $excerpt ); ?>]]></content:encoded>
      <?php endif; ?>
      <?php if ( $source_url ) : ?>
      <atom:link rel="via" href="<?php echo esc_url( $source_url ); ?>" title="Original source" />
      <?php endif; ?>
      <?php if ( $ai_category ) : ?>
      <category><?php echo esc_html( $ai_category ); ?></category>
      <?php endif; ?>
      <?php
      if ( $ai_tags ) {
          foreach ( explode( ',', $ai_tags ) as $tag ) {
              $tag = trim( $tag );
              if ( $tag ) {
                  echo '      <category>' . esc_html( $tag ) . "</category>\n";
              }
          }
      }
      ?>
      <?php if ( $importance ) : ?>
      <media:rating xmlns:media="http://search.yahoo.com/mrss/"><?php echo esc_html( $importance ); ?>/5</media:rating>
      <?php endif; ?>
    </item>
    <?php endwhile; ?>
  </channel>
</rss>
    <?php
    wp_reset_postdata();
    exit;
} );

/**
 * On activation, flush rewrite rules so the new feed endpoints work.
 */
register_activation_hook( __FILE__, function () {
    add_rewrite_rule( '^feed/selected\.xml$', 'index.php?machinery_feed=selected', 'top' );
    add_rewrite_rule( '^feed/all\.xml$',      'index.php?machinery_feed=all',      'top' );
    add_rewrite_rule( '^feed/weekly\.xml$',   'index.php?machinery_feed=weekly',   'top' );
    flush_rewrite_rules();
} );

/**
 * On deactivation, clean up.
 */
register_deactivation_hook( __FILE__, function () {
    flush_rewrite_rules();
} );


/* ----------------------------------------------------------------------
 * PUBLIC JSON API — read-only endpoint for Agent / programmatic access
 *
 *   GET /api/items?category=machines&importance_min=4&since=2026-06-01&q=pouch&limit=20
 *
 * Returns JSON like:
 *   {
 *     "count": 12,
 *     "items": [ { "id": ..., "title": ..., "summary": ..., "category": ...,
 *                  "importance": 4, "tags": [...], "source_url": ...,
 *                  "url": ..., "published_at": ... } ]
 *   }
 * ---------------------------------------------------------------------- */

add_action( 'rest_api_init', function () {
    register_rest_route( 'machinery/v1', '/items', array(
        'methods'             => 'GET',
        'callback'            => 'machinery_api_items',
        'permission_callback' => '__return_true',  // public, read-only
        'args'                => array(
            'category'       => array( 'type' => 'string' ),
            'importance_min' => array( 'type' => 'integer', 'default' => 0 ),
            'since'          => array( 'type' => 'string' ),  // ISO date
            'q'              => array( 'type' => 'string' ),  // search term
            'limit'          => array( 'type' => 'integer', 'default' => 20 ),
            'offset'         => array( 'type' => 'integer', 'default' => 0 ),
        ),
    ) );

    // Stats endpoint — quick health/coverage check.
    register_rest_route( 'machinery/v1', '/stats', array(
        'methods'             => 'GET',
        'callback'            => 'machinery_api_stats',
        'permission_callback' => '__return_true',
    ) );
} );

function machinery_api_items( $request ) {
    $args = array(
        'post_type'      => 'post',
        'post_status'    => 'publish',
        'posts_per_page' => min( 100, max( 1, (int) $request['limit'] ) ),
        'offset'         => max( 0, (int) $request['offset'] ),
        'orderby'        => 'date',
        'order'          => 'DESC',
        'meta_query'     => array(),
    );

    // Category filter (uses our AI category meta slug)
    $category = sanitize_text_field( $request['category'] ?? '' );
    if ( $category ) {
        $args['meta_query'][] = array(
            'key'   => '_machinery_ai_category',
            'value' => $category,
        );
    }

    // Importance min
    $importance_min = (int) $request['importance_min'];
    if ( $importance_min > 0 ) {
        $args['meta_query'][] = array(
            'key'     => '_machinery_ai_importance',
            'value'   => $importance_min,
            'compare' => '>=',
            'type'    => 'NUMERIC',
        );
    }

    // Date filter
    $since = sanitize_text_field( $request['since'] ?? '' );
    if ( $since ) {
        $args['date_query'] = array(
            array(
                'column' => 'post_date',
                'after'  => $since,
            ),
        );
    }

    // Free-text search
    $q = sanitize_text_field( $request['q'] ?? '' );
    if ( $q ) {
        $args['s'] = $q;
    }

    // Must have source URL meta (skip test/manual posts)
    $args['meta_query'][] = array(
        'key'     => '_machinery_source_url',
        'compare' => 'EXISTS',
    );

    $query  = new WP_Query( $args );
    $items  = array();

    while ( $query->have_posts() ) {
        $query->the_post();
        $post_id     = get_the_ID();
        $tags_string = get_post_meta( $post_id, '_machinery_ai_tags', true );
        $items[]     = array(
            'id'              => $post_id,
            'url'             => get_permalink(),
            'title'           => get_the_title(),
            'summary'         => get_post_meta( $post_id, '_machinery_ai_summary', true ),
            'excerpt'         => get_the_excerpt(),
            'category'        => get_post_meta( $post_id, '_machinery_ai_category', true ),
            'importance'      => (int) get_post_meta( $post_id, '_machinery_ai_importance', true ),
            'tags'            => $tags_string ? array_map( 'trim', explode( ',', $tags_string ) ) : array(),
            'buyer_angle'     => get_post_meta( $post_id, '_machinery_ai_buyer_angle', true ),
            'source_url'      => get_post_meta( $post_id, '_machinery_source_url', true ),
            'source_id'       => get_post_meta( $post_id, '_machinery_source_id', true ),
            'published_at'    => mysql2date( 'c', get_post_time( 'Y-m-d H:i:s', true ), false ),
        );
    }
    wp_reset_postdata();

    return new WP_REST_Response( array(
        'count'      => count( $items ),
        'total'      => (int) $query->found_posts,
        'offset'     => (int) $request['offset'],
        'limit'      => (int) $request['limit'],
        'items'      => $items,
    ), 200 );
}

function machinery_api_stats( $request ) {
    global $wpdb;

    $total_posts    = wp_count_posts( 'post' );
    $with_source    = (int) $wpdb->get_var(
        "SELECT COUNT(DISTINCT post_id) FROM {$wpdb->postmeta} WHERE meta_key = '_machinery_source_url'"
    );
    $with_ai        = (int) $wpdb->get_var(
        "SELECT COUNT(DISTINCT post_id) FROM {$wpdb->postmeta} WHERE meta_key = '_machinery_ai_summary'"
    );

    // Per-category counts
    $cats = $wpdb->get_results(
        "SELECT meta_value AS slug, COUNT(DISTINCT post_id) AS n
         FROM {$wpdb->postmeta}
         WHERE meta_key = '_machinery_ai_category'
         GROUP BY meta_value
         ORDER BY n DESC"
    );

    // Importance distribution
    $imp_dist = $wpdb->get_results(
        "SELECT meta_value AS importance, COUNT(DISTINCT post_id) AS n
         FROM {$wpdb->postmeta}
         WHERE meta_key = '_machinery_ai_importance'
         GROUP BY meta_value
         ORDER BY importance DESC"
    );

    return new WP_REST_Response( array(
        'published'       => (int) $total_posts->publish,
        'draft'           => (int) $total_posts->draft,
        'with_source_url' => $with_source,
        'with_ai_summary' => $with_ai,
        'by_category'     => $cats,
        'by_importance'   => $imp_dist,
        'feeds'           => array(
            'selected' => home_url( '/feed/selected.xml' ),
            'all'      => home_url( '/feed/all.xml' ),
            'weekly'   => home_url( '/feed/weekly.xml' ),
        ),
        'api' => array(
            'items' => rest_url( '/machinery/v1/items' ),
            'stats' => rest_url( '/machinery/v1/stats' ),
        ),
    ), 200 );
}
