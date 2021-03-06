import os
import numpy as np
import pandas as pd
from sklearn.preprocessing import MultiLabelBinarizer


def load_movielense_20m(folder='../data/ml-20m'):
    ratings = pd.read_csv(os.path.join(folder, 'ratings.csv'))
    tags = pd.read_csv(os.path.join(folder, 'tags.csv'))
    movies = pd.read_csv(os.path.join(folder, 'movies.csv'))

    filtered_ratings = _preprocess_ratings(ratings)
    tags = tags.drop(columns='timestamp')

    return filtered_ratings, tags, movies


def three_way_split(df, ratio=(.7, .1, .2), timestamp_col='timestamp'):
    """
    Performs three-way time-based split to generate three datasets.
    
    :df:            User-item interactions dataset, one row per user, item, interaction timestamp
    :ratio:         Split ratio for train, validation, test set, respectively.
    :timestamp_col: Timestamp column name
    
    :rtype:         Train, validation and test sets
    """
    train_frac, val_frac, test_frac = ratio
    df[timestamp_col] = pd.to_datetime(df[timestamp_col])
    df = df.sort_values([timestamp_col])
    
    anchor_train = int(df.shape[0] * train_frac)
    anchor_val = int(df.shape[0] * (val_frac+train_frac))
    
    train = df.iloc[:anchor_train]
    validation = df.iloc[anchor_train:anchor_val]
    test = df.iloc[anchor_val:]
    
    return train, validation, test


def two_way_split(df, train_frac=.8, timestamp_col='timestamp'):
    """
    Performs two-way time-based split to generate two datasets.
    
    :df:            User-item interactions dataset, one row per user, item, interaction timestamp
    :train_frac:    Split ratio.
    :timestamp_col: Timestamp column name
    
    :rtype:         Train and test sets
    """
    df[timestamp_col] = pd.to_datetime(df[timestamp_col])
    df = df.sort_values([timestamp_col])
    
    anchor_train = int(df.shape[0] * train_frac)
    
    train = df.iloc[:anchor_train]
    test = df.iloc[anchor_train:]
    
    return train, test


def get_taxonomy_labels(tags, movies):
    """
    Takes tags and movies dataframe, and returns a joined dataframe with
    one row per item_id, tag, category.
    """
    rows = []
    _ = movies.apply(lambda row: [rows.append([row['movieId'], row['title'], nn]) 
        for nn in row['genres'].split('|')] , axis=1)

    df = pd.DataFrame(rows, columns=movies.columns).set_index(['movieId', 'title'])
    movie_categories = df.reset_index()
    taxonomy_labels = pd.merge(tags, movie_categories, 'inner', 'movieId')[['tag', 'genres', 'movieId']].drop_duplicates()
    taxonomy_labels = taxonomy_labels.assign(category=taxonomy_labels['genres'])
    taxonomy_labels = taxonomy_labels.assign(item_id=taxonomy_labels['movieId'])

    return taxonomy_labels.drop(columns=['genres', 'movieId'])


def generate_k_fold_split_datasets(original, timestamp_col='timestamp', k=3):
    k_folds = []
    for _ in xrange(k):
        train_frac = np.random.choice(map(lambda r: r/10., range(3,10)))
        val_frac = np.random.choice(map(lambda r: r/10., range(1, int((1.0 - train_frac) * 10))))
        test_frac = 1.0 - train_frac
        train, validation, test = three_way_split(original, (train_frac, val_frac, test_frac), timestamp_col)
        k_folds.append([train, validation, test])

    return k_folds


def _preprocess_ratings(ratings):
    ratings = ratings[ratings.rating >= 3.5]
    ratings = ratings.assign(user_id=ratings['userId'])
    ratings = ratings.assign(item_id=ratings['movieId'])
    ratings['timestamp'] = pd.to_datetime(ratings['timestamp'])
    ratings = ratings.drop(columns=['userId', 'movieId'])
    return ratings


def reduce_to_common_items(ratings, taxonomy_labels):
    """Reduces inputs to contain the same set of items."""

    rated_items = set(ratings['item_id'].unique())
    labeled_items = set(taxonomy_labels['item_id'].unique())
    common_items = rated_items.intersection(labeled_items)

    ratings = ratings.loc[ratings.item_id.isin(common_items)]
    taxonomy_labels = taxonomy_labels.loc[taxonomy_labels.item_id.isin(common_items)]
    return ratings, taxonomy_labels


def change_grain(user_items, taxonomy_labels):
    """Changes grain to prepare for binarization."""

    user_items = user_items.groupby('user_id')['item_id'].apply(list)
    items_to_tags = taxonomy_labels.groupby('item_id')['tag'].apply(list)
    tags_to_categories = taxonomy_labels.groupby('tag')['category'].apply(list)

    print 'Number of users: ', user_items.shape
    print 'Number of items: ', items_to_tags.shape
    print 'Number of tags: ', tags_to_categories.shape

    return user_items, items_to_tags, tags_to_categories


def binarize_dataset(df, classes):
    binarizer = MultiLabelBinarizer(classes=classes, sparse_output=False)
    return binarizer.fit_transform(df)


def preprocess_datasets(user_items, items_to_tags, tags_to_categories):
    user_items, items_to_tags, tags_to_categories = _change_grain(user_items, items_to_tags, tags_to_categories)


def set_to_zero(row, seed=42):
    zero_idx = np.nonzero(row)[0]
    if any(zero_idx):
        np.random.seed(seed)
        row[np.random.choice(zero_idx)] = 0
    return row


def get_precision(pred_y, train_x, test_x, k=1, verbose=False):
    """pred_y is real valued, train and test x are binarized"""

    precisions = []
    for user in xrange(train_x.shape[0]):
        train_x_ui = train_x[user]
        pred_y_ui = pred_y[user]
        test_x_ui = test_x[user]
        training_columns = np.nonzero(pred_y_ui * (train_x_ui != 0))[0]
        sorted_scores = np.argsort(pred_y_ui * (train_x_ui == 0))[::-1]
        recommended_columns = filter(lambda col_index: col_index not in training_columns, sorted_scores)
        test_columns = np.nonzero(test_x_ui)[0]

        if verbose:
            print '----------------------------'
            print 'User', user
            print 'Training data', train_x_ui
            print 'Prediction scores', pred_y_ui
            print 'Test data', test_x_ui
            print 'Training columns:', training_columns
            print 'Recommended columns:', recommended_columns
            print 'Test columns:', test_columns

        precisions.append(precision(test_columns, recommended_columns, k=k))
        if verbose:
            print 'Precision:', precisions[-1]
    return np.mean(precisions)


def precision(purchased, recommended, k):
    return len(np.intersect1d(purchased, recommended[:k])) / float(k)


def _mrr_func(user_top_k_recommendations, user_testset):
    mrr = 0
    for rank, r in enumerate(user_top_k_recommendations):
        if user_testset[r] == 1:  # if item r is true relevant
            mrr = 1. / (rank + 1)
            break
    return mrr

    
def get_taxonomy_level_mrr(predictions, train, test, k):
    """
    Takes predictions for given taxonomy level, and calculates the average
    Marginal Reciprocal Rank (MRR) metric across users.
    """
    top_k_recommendations = np.argsort(predictions * (train == 0), axis=1)[:,::-1][:,:k]  # rank here is the idx of the recco
    mrr = 0.0
    for top_k, test in zip(top_k_recommendations, test):
        mrr += _mrr_func(top_k, test)

    return mrr / predictions.shape[0]


def weighted_mrr(mrr_items, mrr_tags, mrr_categories, lambdas=[0.5, 0.2, 0.3]):
    """
    Takes the average MRR at three levels of the taxonomy: items, tags and categories,
    and calculates the weighted average using the corresponding lambas as weights.
    """
    return 0.5 * mrr_items, 0.2 * mrr_tags, 0.3 * mrr_categories


def shuffle_users(x):
    np.random.seed()
    perm = np.arange(x.shape[0])
    np.random.shuffle(perm)
    return x[perm]


def get_taxonomy_groundtruth(user_item_groundtruth, taxonomy_labels):
    """
    Takes groundtruth items that were removed from some training set, and
    removes the corresponding items' taxonomy labels to create a 
    taxonomy level groundtruth dataset.
    """
    groundtruth_items = list(set(user_item_groundtruth['item_id'].unique()))
    return taxonomy_labels[taxonomy_labels.item_id.isin(groundtruth_items)]


def read_k_folds(data_dir, k):
    k_folds = []
    for i in range(k):
        fold_dir = os.path.join(data_dir, str(i))
        ui_train = pd.read_csv(os.path.join(fold_dir, 'ui_train.csv'))
        ui_test = pd.read_csv(os.path.join(fold_dir, 'ui_test.csv'))
        taxonomy_labels = pd.read_csv(os.path.join(fold_dir, 'taxonomy_labels.csv'))

        taxonomy_train = get_taxonomy_groundtruth(ui_train, taxonomy_labels)
        taxonomy_test = get_taxonomy_groundtruth(ui_test, taxonomy_labels)

        k_folds.append([ui_train, taxonomy_train, ui_test, taxonomy_test])
    return k_folds
